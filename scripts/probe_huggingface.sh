#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_huggingface.sh — Validate Hugging Face Router API Technical Reference
# (expanded)
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls, no tokens burned).
# HF_TOKEN OPTIONAL — /v1/models is anonymous on HF Router.
#   (Token is needed for /v1/chat/completions and /v1/whoami-v2; this probe
#    only hits the anonymous /v1/models endpoint, so absence of HF_TOKEN is
#    fine — the probe runs with "anonymous" auth.)
# Env fallbacks (first non-empty wins): HF_TOKEN, HUGGING_FACE_HUB_TOKEN,
#   HF_API_KEY.
# Usage:    bash probe_huggingface.sh
# Output:   /tmp/agentkthx_probe_huggingface.json  (raw API response)
#
# Report sections:
#   1. Endpoint            — base URL, auth shape, HTTP status, raw JSON path
#   2. Static API surface   — request params, response shape, tool calling
#                             format (sourced from docs/api/HUGGINGFACE_API_
#                             TECHNICAL_REFERENCE.md; hardcoded — slow-moving)
#   3. Live /v1/models      — top-level keys, total count, sample card
#   4. Card field avail.    — which fields each live model exposes (top-level
#                             AND per-provider sub-blocks, since HF Router
#                             returns richer cards than OpenAI)
#   5. Per-model detail     — id, family, max context, max tokens, size avail,
#                             pricing avail, is_free, is_paid
#   6. Free vs full catalog — free subset, paid subset, static catalog drift,
#                             $0-input+$0-output pricing combos
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

# Token resolution — first non-empty of: HF_TOKEN, HUGGING_FACE_HUB_TOKEN,
# HF_API_KEY. All three are OPTIONAL for the anonymous /v1/models endpoint.
TOKEN="${HF_TOKEN:-${HUGGING_FACE_HUB_TOKEN:-${HF_API_KEY:-}}}"

BASE_URL="${HF_BASE_URL:-https://router.huggingface.co/v1}"

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Endpoint
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}${BOLD}═══ Hugging Face Router API Probe ═══${NC}"
echo ""
echo -e "${CYAN}── 1. Endpoint ──${NC}"
echo "  Base URL:     ${BASE_URL}"
echo "  Models path:  /models  (anonymous — no token required)"
if [ -n "$TOKEN" ]; then
    AUTH_LABEL="Bearer \$HF_TOKEN (len=${#TOKEN})"
    AUTH_MODE="authenticated"
else
    AUTH_LABEL="anonymous (HF_TOKEN / HUGGING_FACE_HUB_TOKEN / HF_API_KEY all unset — /v1/models allows this)"
    AUTH_MODE="anonymous"
fi
echo "  Auth:         ${AUTH_LABEL}"
echo "  Request:      GET ${BASE_URL}/models"

if [ -n "$TOKEN" ]; then
    HTTP_CODE=$(curl -s -m 15 -o /tmp/agentkthx_probe_huggingface.json -w "%{http_code}" \
        -H "Authorization: Bearer $TOKEN" \
        -H "User-Agent: AgentKthx-probe/0.x" \
        -H "Accept: application/json" \
        "${BASE_URL}/models")
else
    HTTP_CODE=$(curl -s -m 15 -o /tmp/agentkthx_probe_huggingface.json -w "%{http_code}" \
        -H "User-Agent: AgentKthx-probe/0.x" \
        -H "Accept: application/json" \
        "${BASE_URL}/models")
fi

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "  ${RED}✗ HTTP ${HTTP_CODE}${NC}"
    echo "  Response body (first 600 bytes):"
    head -c 600 /tmp/agentkthx_probe_huggingface.json
    echo
    exit 1
fi
echo -e "  ${GREEN}✓ HTTP 200${NC}  (auth=${AUTH_MODE}, raw JSON: /tmp/agentkthx_probe_huggingface.json)"

python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_huggingface.json')); assert isinstance(d.get('data'), list)" 2>/dev/null || {
    echo -e "  ${RED}✗ Response is not OpenAI-shaped {\"data\": [...]}.${NC}"
    head -c 600 /tmp/agentkthx_probe_huggingface.json
    echo
    exit 1
}

# ═══════════════════════════════════════════════════════════════════════════
# Sections 2–6 — Python analysis (single heredoc, parses JSON + emits report)
# ═══════════════════════════════════════════════════════════════════════════
python3 << 'PYEOF'
import json
import re
from collections import Counter

CYAN = '\033[0;36m'; GREEN = '\033[0;32m'; YELLOW = '\033[0;33m'; RED = '\033[0;31m'; NC = '\033[0m'

d = json.load(open("/tmp/agentkthx_probe_huggingface.json"))
models = d.get("data", [])

# ───────────────────────────────────────────────────────────────────────────
# Section 2 — Static API surface (sourced from docs/api/HUGGINGFACE_API_
# TECHNICAL_REFERENCE.md). Hardcoded in the probe because the doc is slow-
# moving and parsing it inline would be brittle. Cite the doc so the source
# of truth is visible.
# ───────────────────────────────────────────────────────────────────────────
REQUEST_PARAMS = [
    # (name, type, required, default, notes)
    ("model",              "string",            True,  None,    "HF model id; may carry routing suffix :fastest/:cheapest/:preferred/:<partner>"),
    ("messages",           "array[Message]",    True,  None,    "OpenAI shape; roles system/user/assistant/tool; content string or multimodal array"),
    ("temperature",        "float",             False, "0.7",   "0.0-2.0"),
    ("top_p",              "float",             False, "1.0",   "Nucleus sampling"),
    ("top_k",              "int",               False, "0",     "HF forwards (some providers honor on router path)"),
    ("max_tokens",         "int",               False, None,    "OpenAI-style; AgentKthx caps at ctx//32"),
    ("max_completion_tokens","int",             False, None,    "OpenAI newer field; many partner providers may not honor"),
    ("n",                  "int",               False, "1",     "Most partner providers cap at 1"),
    ("stream",             "bool",              False, "false", "SSE stream"),
    ("stream_options",     "object",            False, None,    "{include_usage: true} — set automatically when streaming; some :cheapest providers don't return usage chunk"),
    ("stop",               "string | array",    False, None,    "Up to 4 strings"),
    ("seed",               "int",               False, None,    "Best-effort reproducibility"),
    ("presence_penalty",   "float",             False, "0.0",   "Router only"),
    ("frequency_penalty",  "float",             False, "0.0",   "Router only"),
    ("repetition_penalty", "float",             False, "1.0",   "TGI + some router providers; multiplier (1.0=none, >1.0 discourages)"),
    ("response_format",    "object",            False, None,    "{type: text|json_object|json_schema, schema?}"),
    ("tools",              "array[Tool]",       False, None,    "OpenAI tool schema"),
    ("tool_choice",        "string | object",   False, "auto",  "auto|none|required|{type:function,function:{name:X}}"),
    ("reasoning_effort",   "string",            False, None,    "For thinking models (Qwen3-Thinking, DeepSeek-R1, gpt-oss-20b-reasoning)"),
    ("user",               "string",            False, None,    "OpenAI-style user id"),
]

RESPONSE_PARAMS = [
    # (name, type, notes)
    ("id",                 "string",            "e.g. chatcmpl-xxxxxxxxxxxx"),
    ("object",             "string",            "'chat.completion'"),
    ("created",            "int (unix ts)",     "Response timestamp"),
    ("model",              "string",            "Model id WITHOUT routing suffix"),
    ("choices",            "array[Choice]",     "One per n"),
    ("choices[].message",  "object",            "{role:'assistant', content, reasoning?, tool_calls?}"),
    ("choices[].message.reasoning","string",    "HF-SPECIFIC — chain-of-thought for reasoning models (some providers use reasoning_content)"),
    ("choices[].finish_reason", "string",       "stop|tool_calls|length|content_filter|model_context_window_exceeded(HF)"),
    ("usage",              "object",            "{prompt_tokens, completion_tokens, total_tokens}"),
    ("error",              "object (optional)", "HF-SPECIFIC — provider-side error on HTTP 200 (upstream rate-limited)"),
]

TOOL_CALLING = {
    "format": "OpenAI-style tools[] array (function-calling convention)",
    "tool_choice": "auto|none|required|{type:function,function:{name:X}} (NO 'any' alias like Mistral)",
    "tool_call_id_format": "call_<alphanumeric> (OpenAI-style with 'call_' prefix)",
    "arguments_format": "JSON string (parsed by client; fallback to {_raw_arguments: ...} on decode failure)",
    "parallel_tool_calls": "NOT forwarded by HF backend (unlike OpenAI/Mistral)",
    "message_shape": (
        '{"role":"assistant","content":"...","tool_calls":[{"id":"call_abc123",'
        '"type":"function","function":{"name":"get_weather","arguments":"{\\"city\\":\\"NYC\\"}"}}]}'
    ),
    "tool_message": (
        '{"role":"tool","content":"result","tool_call_id":"call_abc123","name":"get_weather"}'
    ),
    "provider_support_matrix": "Together/Fireworks/Groq/Novita/Cerebras=yes; HF Inference=partial(TGI only); Replicate/Fal AI/Baseten=varies",
    "react_fallback": "Provider rejects tools field -> backend retries without tools/tool_choice",
    "doc_ref": "docs/api/HUGGINGFACE_API_TECHNICAL_REFERENCE.md §Function Calling Implementation",
}

print(f"\n{CYAN}── 2. Static API surface{NC}  (from docs/api/HUGGINGFACE_API_TECHNICAL_REFERENCE.md)")
print(f"  {CYAN}[Request params — POST /chat/completions body]{NC}")
print(f"    {'Name':26s} {'Type':22s} {'Req':4s} {'Default':14s} Notes")
print(f"    {'─'*26} {'─'*22} {'─'*4} {'─'*14} {'─'*40}")
for name, typ, req, default, notes in REQUEST_PARAMS:
    req_s = "yes" if req else "no"
    default_s = str(default) if default is not None else "—"
    print(f"    {name:26s} {typ:22s} {req_s:4s} {default_s:14s} {notes}")

print(f"\n  {CYAN}[Response shape — chat.completion object]{NC}")
print(f"    {'Field':36s} {'Type':22s} Notes")
print(f"    {'─'*36} {'─'*22} {'─'*40}")
for name, typ, notes in RESPONSE_PARAMS:
    print(f"    {name:36s} {typ:22s} {notes}")

print(f"\n  {CYAN}[Tool calling format]{NC}")
print(f"    format:                {TOOL_CALLING['format']}")
print(f"    tool_choice:           {TOOL_CALLING['tool_choice']}")
print(f"    tool_call_id_format:  {TOOL_CALLING['tool_call_id_format']}")
print(f"    arguments_format:     {TOOL_CALLING['arguments_format']}")
print(f"    parallel_tool_calls:  {TOOL_CALLING['parallel_tool_calls']}")
print(f"    message_shape:        {TOOL_CALLING['message_shape']}")
print(f"    tool_message:         {TOOL_CALLING['tool_message']}")
print(f"    provider_support:     {TOOL_CALLING['provider_support_matrix']}")
print(f"    react_fallback:       {TOOL_CALLING['react_fallback']}")
print(f"    doc_ref:              {TOOL_CALLING['doc_ref']}")

# ───────────────────────────────────────────────────────────────────────────
# Section 3 — Live /v1/models response
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 3. Live /v1/models response ──{NC}")
print(f"  Total models:           {len(models)}")
print(f"  Top-level keys:        {list(d.keys())}")
if models:
    sample = models[0]
    print(f"  Sample model object:   {sample.get('id', '?')}")
    print(f"  Sample card keys:      {list(sample.keys())}")
    providers_count = sum(len(m.get("providers") or []) for m in models)
    print(f"  Total (model,provider) combos: {providers_count}")
    unique_providers = sorted({
        p.get("provider", "?")
        for m in models
        for p in (m.get("providers") or [])
    })
    print(f"  Unique partner providers ({len(unique_providers)}):")
    # Wrap at ~78 cols
    line = "    "
    for prov in unique_providers:
        if len(line) + len(prov) + 2 > 78:
            print(line.rstrip())
            line = "    "
        line += prov + ", "
    print(line.rstrip(", "))

# ───────────────────────────────────────────────────────────────────────────
# Section 4 — Card field availability (TWO sub-blocks: top-level card
# fields, then per-provider fields, since HF Router returns richer cards
# than OpenAI — each model card embeds a providers[] array of per-partner
# pricing/capability info).
# Non-chat filter: NONE — HF Router /v1/models only serves chat-capable
# models (unlike OpenAI, which mixes chat/embed/whisper).
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 4. Card field availability (live models) ──{NC}")
print(f"  Non-chat filter applied by HF Router: NONE (/v1/models only serves chat-capable models)")

# (a) Top-level card fields
STANDARD_FIELDS = ["id", "object", "created", "owned_by"]
RICH_TOPLEVEL_FIELDS = [
    "context_length", "max_completion_tokens", "supported_parameters",
    "architecture", "providers",
]
TOPLEVEL_FIELDS = STANDARD_FIELDS + RICH_TOPLEVEL_FIELDS
print(f"\n  {CYAN}(a) Top-level card fields{NC}  ({len(models)} model cards)")
print(f"    {'Field':28s} {'Count':12s} {'Status':12s}")
print(f"    {'─'*28} {'─'*12} {'─'*12}")
for field in TOPLEVEL_FIELDS:
    n = sum(1 for m in models if field in m)
    if n == 0:
        status = f"{YELLOW}○ missing{NC}"
    elif n == len(models):
        status = f"{GREEN}✓ all{NC}"
    else:
        status = f"{YELLOW}~ partial{NC}"
    print(f"    {field:28s} {n}/{len(models):<10d}  {status}")

# (b) Per-provider fields (across all (model, provider) combos)
PER_PROVIDER_FIELDS = [
    "provider", "status", "context_length", "pricing", "is_free",
    "supports_tools", "supports_structured_output", "first_token_latency_ms",
    "throughput", "is_model_author", "max_completion_tokens",
]
all_combos = [
    (m.get("id", "?"), p)
    for m in models
    for p in (m.get("providers") or [])
]
total_combos = len(all_combos)
print(f"\n  {CYAN}(b) Per-provider fields{NC}  ({total_combos} (model,provider) combos)")
print(f"    {'Field':32s} {'Count':14s} {'Status':12s}")
print(f"    {'─'*32} {'─'*14} {'─'*12}")
for field in PER_PROVIDER_FIELDS:
    n = sum(1 for _, p in all_combos if field in p)
    if total_combos == 0:
        status = "—"
    elif n == 0:
        status = f"{YELLOW}○ missing{NC}"
    elif n == total_combos:
        status = f"{GREEN}✓ all{NC}"
    else:
        status = f"{YELLOW}~ partial{NC}"
    print(f"    {field:32s} {n}/{total_combos:<12d}  {status}")

# Availability summary for the per-model detail in Section 5
SIZE_AVAILABLE = False  # HF cards don't expose top-level size; always "—"
PRICING_AVAILABLE = any(
    "pricing" in p for m in models for p in (m.get("providers") or [])
)
MAXCTX_AVAILABLE = any(
    isinstance(p.get("context_length"), int)
    for m in models for p in (m.get("providers") or [])
) or any(isinstance(m.get("context_length"), int) for m in models)
MAXTOK_AVAILABLE = any(
    isinstance(p.get("max_completion_tokens"), int)
    for m in models for p in (m.get("providers") or [])
) or any(isinstance(m.get("max_completion_tokens"), int) for m in models)
print(f"\n  Availability summary (for per-model detail):")
print(f"    Model size available:        {'yes' if SIZE_AVAILABLE else 'no'}  (HF cards don't expose size — show '—')")
print(f"    Pricing available:           {'yes' if PRICING_AVAILABLE else 'no'}  (live API is source of truth — per-provider)")
print(f"    Max context available:        {'yes' if MAXCTX_AVAILABLE else 'no'}  (live API is source of truth — per-provider max)")
print(f"    Max tokens available:         {'yes' if MAXTOK_AVAILABLE else 'no'}  (live API is source of truth — per-provider max)")
print(f"    Pricing units:               USD per 1M tokens (no normalization needed)")

# ───────────────────────────────────────────────────────────────────────────
# Section 5 — Per-model detail
# Free-tier rule mirrors _is_free_model() + _is_free_model_live() in
# agentkthx/plugins/huggingface/huggingface.py. Two layers:
#   (1) Static: model_id (with :suffix stripped) in HF_FREE_MODEL_WHITELIST
#       (3 entries — verified $0/token pricing).
#   (2) Live: any provider in the live card's providers[] has is_free=true.
# As of 2026-09-26: is_free is false for all 336 combos even with auth —
# the free-tier model is purely credit-based ($0.10/mo credit). The 3
# whitelist entries are the only genuinely free models (verified $0/token
# pricing). Catalog has NO pricing field — live API is source of truth.
# ───────────────────────────────────────────────────────────────────────────
CATALOG = {
    "openai/gpt-oss-120b":                    {"ctx": 131072, "max_tok": 32768, "provider": "openai"},
    "openai/gpt-oss-20b":                     {"ctx": 131072, "max_tok": 32768, "provider": "openai"},
    "prism-ml/Ternary-Bonsai-27B-gguf":        {"ctx": 131072, "max_tok": 4096,  "provider": "prism-ml"},
    "prism-ml/Ternary-Bonsai-27B-AWQ-4bit":    {"ctx": 131072, "max_tok": 4096,  "provider": "prism-ml"},
    "Qwen/Qwen3-4B-Thinking-2507":             {"ctx": 32768,  "max_tok": 8192,  "provider": "qwen",  "supports_thinking": True},
    "Qwen/Qwen3-Coder-480B-A35B-Instruct":    {"ctx": 262144, "max_tok": 32768, "provider": "qwen"},
    "Qwen/Qwen2.5-72B-Instruct":              {"ctx": 131072, "max_tok": 8192,  "provider": "qwen"},
    "deepseek-ai/DeepSeek-R1":                 {"ctx": 65536,  "max_tok": 32768, "provider": "deepseek", "supports_thinking": True},
    "deepseek-ai/DeepSeek-V3.1":               {"ctx": 131072, "max_tok": 32768, "provider": "deepseek"},
    "meta-llama/Llama-3.3-70B-Instruct":       {"ctx": 131072, "max_tok": 8192,  "provider": "meta"},
    "google/gemma-3-27b-it":                   {"ctx": 32768,  "max_tok": 4096,  "provider": "google"},
    "google/gemma-3-12b-it":                   {"ctx": 32768,  "max_tok": 4096,  "provider": "google"},
    "zai-org/GLM-4.5":                         {"ctx": 131072, "max_tok": 8192,  "provider": "zai"},
    "zai-org/GLM-4.5-Air":                     {"ctx": 131072, "max_tok": 8192,  "provider": "zai"},
    "inclusionAI/Ling-3.0-flash-Fin":          {"ctx": 131072, "max_tok": 8192,  "provider": "inclusionai"},
    "mistralai/Mistral-Nemo-Instruct-2407":    {"ctx": 131072, "max_tok": 8192,  "provider": "mistral"},
    "microsoft/Phi-4-mini-instruct":           {"ctx": 131072, "max_tok": 8192,  "provider": "microsoft"},
    "CohereForAI/c4ai-command-r-plus-08-2024": {"ctx": 131072, "max_tok": 8192,  "provider": "cohere"},
}

HF_FREE_MODEL_WHITELIST = frozenset({
    "inclusionAI/Ling-3.0-flash-Fin",
    "prism-ml/Ternary-Bonsai-27B-gguf",
    "prism-ml/Ternary-Bonsai-27B-AWQ-4bit",
})

def catalog_key(live_id):
    """Strip routing suffix (:fastest/:cheapest/:preferred/:<partner>)."""
    return live_id.split(":", 1)[0]

def catalog_lookup(live_id):
    """Look up live_id in CATALOG after stripping routing suffix."""
    k = catalog_key(live_id)
    if k in CATALOG:
        return k, CATALOG[k]
    return None, None

def is_free(model_id, live_card=None):
    """Mirror of _is_free_model() + _is_free_model_live() in huggingface.py.

    Two layers:
      (1) Static: model_id (with :suffix stripped) in HF_FREE_MODEL_WHITELIST.
      (2) Live: any provider in the live card's providers[] has is_free=true.
    """
    base = model_id.split(":", 1)[0]
    if base in HF_FREE_MODEL_WHITELIST:
        return True
    if live_card:
        for p in (live_card.get("providers") or []):
            if p.get("is_free", False):
                return True
    return False

def family_of(model_id):
    """Org prefix from model id (e.g. 'Qwen/Qwen3-Coder-480B-A35B-Instruct' -> 'Qwen')."""
    if "/" in model_id:
        return model_id.split("/", 1)[0]
    return "—"

def live_max_ctx(m_card):
    """Max context_length across live card's providers[], else top-level field."""
    ctxs = [
        p.get("context_length")
        for p in (m_card.get("providers") or [])
        if isinstance(p.get("context_length"), int)
    ]
    if ctxs:
        return max(ctxs)
    top = m_card.get("context_length")
    if isinstance(top, int):
        return top
    return None

def live_max_tok(m_card):
    """Max max_completion_tokens across live card's providers[], else top-level field."""
    toks = [
        p.get("max_completion_tokens")
        for p in (m_card.get("providers") or [])
        if isinstance(p.get("max_completion_tokens"), int)
    ]
    if toks:
        return max(toks)
    top = m_card.get("max_completion_tokens")
    if isinstance(top, int):
        return top
    return None

def live_cheapest_pricing(m_card):
    """Cheapest (input, output) USD/1M tokens across providers with pricing.

    Returns (in_price, out_price) or (None, None).
    """
    priced = []
    for p in (m_card.get("providers") or []):
        pr = p.get("pricing") or {}
        in_r = pr.get("input")
        out_r = pr.get("output")
        if isinstance(in_r, (int, float)) and isinstance(out_r, (int, float)):
            priced.append((float(in_r), float(out_r)))
    if not priced:
        return None, None
    # Cheapest by sum of input + output per 1M tokens
    priced.sort(key=lambda x: x[0] + x[1])
    return priced[0]

def get_ctx(model_id, m_card):
    """Live max context_length across providers, fall back to catalog 'ctx'."""
    live = live_max_ctx(m_card)
    if live is not None:
        return f"{live:,}"
    _, meta = catalog_lookup(model_id)
    if meta and meta.get("ctx"):
        return f"{meta['ctx']:,}(cat)"
    return "—"

def get_max_tok(model_id, m_card):
    """Live max_completion_tokens across providers, fall back to catalog 'max_tok'."""
    live = live_max_tok(m_card)
    if live is not None:
        return f"{live:,}"
    _, meta = catalog_lookup(model_id)
    if meta and meta.get("max_tok"):
        return f"{meta['max_tok']:,}(cat)"
    return "—"

def get_size(m_card):
    """HF cards don't expose size; always '—'."""
    return "—"

def get_pricing(model_id, m_card):
    """Live cheapest (in,out) USD/1M from providers[]. No catalog fallback
    (catalog has no pricing field — live API is the source of truth)."""
    in_r, out_r = live_cheapest_pricing(m_card)
    if in_r is None:
        _, meta = catalog_lookup(model_id)
        if meta is None:
            return "—"
        return "no live pricing"
    if in_r == 0.0 and out_r == 0.0:
        return f"$0/$0 (live)"
    return f"${in_r:.4f}/${out_r:.4f}"

print(f"\n{CYAN}── 5. Per-model detail ──{NC}")
print(f"  (Free rule: model_id (suffix-stripped) in HF_FREE_MODEL_WHITELIST OR")
print(f"   any live provider has is_free=true. As of 2026-09-26, is_free is")
print(f"   false for all 336 combos — only the 3 whitelist entries are free.)")
print(f"  (Pricing: live API per-provider USD/1M tokens, cheapest across providers)")
print(f"  (Ctx/MaxTok: max across live providers[]; '(cat)' suffix = static catalog fallback)")
print()
print(f"  {'Model':48s} {'Family':18s} {'Ctx':12s} {'MaxTok':12s} {'Size':6s} {'Pricing':22s} {'Free':5s} {'Paid':5s}")
print(f"  {'─'*48} {'─'*18} {'─'*12} {'─'*12} {'─'*6} {'─'*22} {'─'*5} {'─'*5}")
for m in sorted(models, key=lambda x: x.get("id", "")):
    mid = m.get("id", "?")
    fam = family_of(mid)
    ctx = get_ctx(mid, m)
    max_tok = get_max_tok(mid, m)
    size = get_size(m)
    pricing = get_pricing(mid, m)
    free = is_free(mid, m)
    paid = not free
    free_s = f"{GREEN}yes{NC}" if free else "no"
    paid_s = "yes" if paid else f"{GREEN}—{NC}"
    print(f"  {mid:48s} {fam:18s} {ctx:12s} {max_tok:12s} {size:6s} {pricing:22s} {free_s:5s} {paid_s:5s}")

# ───────────────────────────────────────────────────────────────────────────
# Section 6 — Free vs full catalog
# ───────────────────────────────────────────────────────────────────────────
free_models = [m for m in models if is_free(m.get("id", ""), m)]
paid_models = [m for m in models if not is_free(m.get("id", ""), m)]

print(f"\n{CYAN}── 6. Free vs full catalog ──{NC}")
print(f"  Live total:              {len(models)}")
print(f"  Free (is_free=yes):     {len(free_models)}")
for m in free_models:
    print(f"    {GREEN}✓{NC} {m.get('id', '?')}")
if not free_models:
    print(f"    (none surfaced — HF_FREE_MODEL_WHITELIST has 3 entries, see below)")
print(f"  Paid (is_paid=yes):     {len(paid_models)}")
if paid_models:
    for m in paid_models[:5]:
        print(f"    · {m.get('id', '?')}")
    if len(paid_models) > 5:
        print(f"    ... + {len(paid_models) - 5} more")

# Free split: whitelist vs live is_free flag
whitelist_in_live = [
    m for m in models
    if m.get("id", "").split(":", 1)[0] in HF_FREE_MODEL_WHITELIST
]
live_is_free_true = [
    m for m in models
    if any(p.get("is_free", False) for p in (m.get("providers") or []))
]
print(f"\n  Free split (two layers per _is_free_model_live):")
print(f"    Whitelist matches in live API:    {len(whitelist_in_live)}/{len(HF_FREE_MODEL_WHITELIST)}")
for m in whitelist_in_live:
    print(f"      {GREEN}✓{NC} {m.get('id', '?')}")
for w in sorted(HF_FREE_MODEL_WHITELIST):
    if w not in {m.get("id", "").split(":", 1)[0] for m in models}:
        print(f"      {YELLOW}○ {w}  (whitelisted but not in live API — fallback){NC}")
print(f"    Live is_free=true combos:        {len(live_is_free_true)}")
if live_is_free_true:
    for m in live_is_free_true[:10]:
        provs = [p.get("provider", "?") for p in (m.get("providers") or []) if p.get("is_free")]
        print(f"      {GREEN}✓{NC} {m.get('id', '?')} via {','.join(provs)}")
else:
    print(f"      (none — as of 2026-09-26, is_free is false for all 336 combos)")
    print(f"       Free-tier is purely credit-based ($0.10/mo credit) per HF docs.")

# $0 pricing combos (genuinely free at the pricing level — even if is_free
# flag is false; HF's flag is conservative and doesn't reflect promo pricing)
zero_pricing_combos = []
for m in models:
    for p in (m.get("providers") or []):
        pr = p.get("pricing") or {}
        in_r = pr.get("input")
        out_r = pr.get("output")
        if isinstance(in_r, (int, float)) and isinstance(out_r, (int, float)):
            if float(in_r) == 0.0 and float(out_r) == 0.0:
                zero_pricing_combos.append((m.get("id", "?"), p.get("provider", "?")))
print(f"\n  Bonus: $0 input + $0 output pricing combos  ({len(zero_pricing_combos)} of {total_combos})")
print(f"  (These are genuinely $0/token regardless of is_free flag — HF's flag is")
print(f"   conservative. The 3 whitelist entries come from this set.)")
for mid, prov in zero_pricing_combos[:20]:
    in_whitelist = mid.split(":", 1)[0] in HF_FREE_MODEL_WHITELIST
    marker = f"{GREEN}✓{NC} (whitelist)" if in_whitelist else "·"
    print(f"    {marker} {mid:50s} via {prov}")
if len(zero_pricing_combos) > 20:
    print(f"    ... + {len(zero_pricing_combos) - 20} more")

# Catalog drift — most live models won't match the 18-entry catalog; they're
# classified via live pricing, not catalog. Catalog is a fallback when
# /v1/models is unreachable, NOT the source of truth for HF (unlike Mistral).
matched_keys = set()
for m in models:
    if not m.get("id"):
        continue
    k, _ = catalog_lookup(m["id"])
    if k:
        matched_keys.add(k)
unmatched_live_ids = sorted({
    m.get("id", "") for m in models
    if m.get("id") and not catalog_lookup(m["id"])[0]
})
in_live_not_catalog = unmatched_live_ids
in_catalog_not_live = sorted(set(CATALOG.keys()) - matched_keys)

print(f"\n  Static HF_MODELS catalog size:       {len(CATALOG)}")
print(f"  In live API but NOT in catalog:      {len(in_live_not_catalog)}")
print(f"  (Most live models won't match — catalog is a fallback when")
print(f"   /v1/models is unreachable, NOT the source of truth. Unmatched")
print(f"   models are → classified via live pricing, not catalog.)")
for m in in_live_not_catalog[:20]:
    print(f"    + {m}")
if len(in_live_not_catalog) > 20:
    print(f"    ... + {len(in_live_not_catalog) - 20} more")
print(f"  In catalog but NOT in live API:      {len(in_catalog_not_live)}")
for m in in_catalog_not_live:
    print(f"    - {m}")

# Catalog free subset (entries in HF_FREE_MODEL_WHITELIST)
catalog_free = sorted(k for k in CATALOG if k.split(":", 1)[0] in HF_FREE_MODEL_WHITELIST)
catalog_paid = sorted(k for k in CATALOG if k.split(":", 1)[0] not in HF_FREE_MODEL_WHITELIST)
print(f"\n  Catalog free subset (whitelist ∩ catalog):  {len(catalog_free)}")
for m in catalog_free:
    print(f"    {GREEN}✓{NC} {m}")
print(f"  Catalog paid subset:                        {len(catalog_paid)}")

# No 'deprecation' field on HF cards (unlike Mistral).
deprecation_field = any("deprecation" in m for m in models)
print(f"\n  Deprecation field present on cards: {deprecation_field}  (HF Router does NOT expose a 'deprecation' field — no early-warning possible)")

print(f"\n{CYAN}{'─'*78}{NC}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_huggingface.json")
print(f"{CYAN}{'─'*78}{NC}")
PYEOF
