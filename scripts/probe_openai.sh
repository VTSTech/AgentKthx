#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_openai.sh — Validate OpenAI API Technical Reference (expanded)
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls, no tokens burned).
# Requires: OPENAI_API_KEY in env (sk-proj- recommended)
# Usage:    bash probe_openai.sh
# Output:   /tmp/agentkthx_probe_openai.json  (raw API response)
#
# Report sections:
#   1. Endpoint           — base URL, auth shape, HTTP status, raw JSON path
#   2. Static API surface  — request params, response shape, tool calling
#                            format (sourced from docs/api/OPENAI_API_
#                            TECHNICAL_REFERENCE.md; hardcoded — slow-moving)
#   3. Live /v1/models    — top-level keys, total count, sample card
#   4. Card field avail.   — which fields each live model exposes
#                            (OpenAI cards are MINIMAL: id/object/created/
#                            owned_by only — no context_length, no pricing)
#   5. Per-model detail     — id, family, tier, ctx, max_tok, size, pricing,
#                            is_free (always no since R07.03), free_tier_eligible
#                            (catalog flag — separate concept), is_paid
#   6. Free vs full catalog — free subset (empty since R07.03),
#                            free_tier_eligible subset (4 entries),
#                            static catalog drift, deprecation warnings
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

API_KEY="${OPENAI_API_KEY:-}"
[ -z "$API_KEY" ] && echo "ERROR: Set OPENAI_API_KEY first: export OPENAI_API_KEY=sk-proj-..." >&2 && exit 1

BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com/v1}"

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Endpoint
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}${BOLD}═══ OpenAI API Technical Reference Validation Probe ═══${NC}"
echo ""
echo -e "${CYAN}── 1. Endpoint ──${NC}"
echo "  Base URL:     ${BASE_URL}"
echo "  Models path:  /models (auth required — unlike HF Router)"
echo "  Auth:         Bearer \$OPENAI_API_KEY (len=${#API_KEY})"
echo "  Request:      GET ${BASE_URL}/models"

HTTP_CODE=$(curl -s -m 15 -o /tmp/agentkthx_probe_openai.json -w "%{http_code}" \
    -H "Authorization: Bearer $API_KEY" \
    -H "User-Agent: AgentKthx-probe/0.x" \
    -H "Accept: application/json" \
    "${BASE_URL}/models")

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "  ${RED}✗ HTTP ${HTTP_CODE}${NC}"
    echo "  Response body (first 600 bytes):"
    head -c 600 /tmp/agentkthx_probe_openai.json
    echo
    exit 1
fi
echo -e "  ${GREEN}✓ HTTP 200${NC}  (raw JSON: /tmp/agentkthx_probe_openai.json)"

python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_openai.json')); assert isinstance(d.get('data'), list)" 2>/dev/null || {
    echo -e "  ${RED}✗ Response is not OpenAI-shaped {\"data\": [...]}.${NC}"
    head -c 600 /tmp/agentkthx_probe_openai.json
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

d = json.load(open("/tmp/agentkthx_probe_openai.json"))
models = d.get("data", [])

# ───────────────────────────────────────────────────────────────────────────
# Section 2 — Static API surface (sourced from docs/api/OPENAI_API_TECHNICAL_REFERENCE.md)
# Hardcoded in the probe because the doc is slow-moving and parsing it inline
# would be brittle. Cite the doc so the source of truth is visible.
# ───────────────────────────────────────────────────────────────────────────
REQUEST_PARAMS = [
    # (name, type, required, default, notes)
    ("model",              "string",            True,  None,    "Model id from /v1/models"),
    ("messages",           "array[Message]",    True,  None,    "Roles: system/developer/user/assistant/tool/function; content may be string or multimodal array"),
    ("temperature",        "float",             False, "1.0",   "Range 0.0-2.0"),
    ("top_p",              "float",             False, "1.0",   "Nucleus sampling"),
    ("top_k",              "int",               False, "-1",    "Not honored by all models"),
    ("seed",               "int",               False, None,    "Best-effort reproducibility"),
    ("max_tokens",         "int",               False, None,    "Legacy — reasoning models reject this"),
    ("max_completion_tokens","int",             False, None,    "OpenAI-preferred. Includes reasoning tokens"),
    ("n",                  "int",               False, "1",     "Ignored for reasoning models (n=1 only)"),
    ("stop",               "string | array",    False, None,    "Up to 4 strings; not supported on reasoning models"),
    ("stream",             "bool",              False, "false", "SSE stream"),
    ("stream_options",     "object",            False, None,    "{include_usage: bool}"),
    ("presence_penalty",   "float",             False, "0.0",   "Range -2.0 to 2.0"),
    ("frequency_penalty",  "float",             False, "0.0",   "Range -2.0 to 2.0"),
    ("logit_bias",         "dict",              False, "{}",    "token_id → bias (-100..+100); ignored on reasoning models"),
    ("logprobs",           "bool",              False, "false", "Not supported on reasoning models"),
    ("top_logprobs",       "int (0-20)",        False, None,    "Requires logprobs:true"),
    ("response_format",    "object",            False, None,    "{type: text|json_object|json_schema}"),
    ("tools",              "array[Tool]",       False, None,    "OpenAI function-calling tool schemas"),
    ("tool_choice",        "string | object",   False, "auto",  "auto|none|required|{type:function,function:{name:X}}"),
    ("parallel_tool_calls","bool",              False, "true",  "Allow parallel tool calls"),
    ("reasoning_effort",   "string",            False, None,    "none|minimal|low|medium|high|xhigh|max (thinking models only)"),
    ("service_tier",       "string",            False, "auto",  "auto|default|flex|scale|priority|fast"),
    ("user",               "string",            False, None,    "End-user id for abuse monitoring"),
    ("metadata",           "map",               False, None,    "Free-form key/value"),
    ("modalities",         "list[str]",         False, '["text"]', '["text","audio"] or ["audio"]'),
    ("audio",              "object",            False, None,    "{voice, format} for audio output"),
    ("web_search_options", "object",            False, None,    "{search_context_size: low|medium|high}"),
    ("store",              "bool",              False, "true",  "Server-side response storage"),
    ("prompt_cache_options","object",           False, None,    "{ttl: default|extended}"),
    ("prediction",         "object",            False, None,    "{type:content, content:...} predicted output"),
]

RESPONSE_PARAMS = [
    # (name, type, notes)
    ("id",                 "string",            "e.g. chatcmpl-xxxxxxxxxxxx"),
    ("object",             "string",            "'chat.completion'"),
    ("created",            "int (unix ts)",     "Response timestamp"),
    ("model",              "string",            "Model id used (may include snapshot suffix)"),
    ("choices",            "array[Choice]",     "One per n; reasoning models force n=1"),
    ("choices[].message",  "object",            "{role:'assistant', content, refusal?, annotations?, audio?, tool_calls?}"),
    ("choices[].finish_reason", "string",       "stop|length|tool_calls|content_filter|function_call(deprecated)"),
    ("usage",              "object",            "{prompt_tokens, completion_tokens, total_tokens, prompt_tokens_details.cached_tokens, completion_tokens_details.reasoning_tokens}"),
    ("service_tier",       "string",            "Echoes actual tier used (may differ if 'auto')"),
    ("system_fingerprint", "string",            "Backend config fingerprint (pair with seed for determinism)"),
]

TOOL_CALLING = {
    "format": "OpenAI-style tools[] array (function-calling convention) — the canonical spec",
    "tool_choice": "auto|none|required|{type:function,function:{name:X}}",
    "tool_call_id_format": "String WITH 'call_' prefix (e.g. 'call_abc123') — canonical OpenAI format",
    "arguments_format": "JSON string (must be parsed by client)",
    "parallel_tool_calls": "bool, default true — multiple tool_calls per assistant turn allowed",
    "message_shape": '{"role":"assistant","content":"...","tool_calls":[{"id":"call_abc123","type":"function","function":{"name":"get_weather","arguments":"{\\"city\\":\\"NYC\\"}"}}]}',
    "tool_message": '{"role":"tool","content":"result","tool_call_id":"call_abc123","name":"get_weather"}',
    "built_in_tools_responses_api_only": "web_search (via web_search_options on Chat Completions), file_search, code_interpreter, computer_use (Responses API only)",
    "doc_ref": "docs/api/OPENAI_API_TECHNICAL_REFERENCE.md",
}

print(f"\n{CYAN}── 2. Static API surface{NC}  (from docs/api/OPENAI_API_TECHNICAL_REFERENCE.md)")
print(f"  {CYAN}[Request params — POST /chat/completions body]{NC}")
print(f"    {'Name':28s} {'Type':22s} {'Req':4s} {'Default':14s} Notes")
print(f"    {'─'*28} {'─'*22} {'─'*4} {'─'*14} {'─'*40}")
for name, typ, req, default, notes in REQUEST_PARAMS:
    req_s = "yes" if req else "no"
    default_s = str(default) if default is not None else "—"
    print(f"    {name:28s} {typ:22s} {req_s:4s} {default_s:14s} {notes}")

print(f"\n  {CYAN}[Response shape — chat.completion object]{NC}")
print(f"    {'Field':28s} {'Type':20s} Notes")
print(f"    {'─'*28} {'─'*20} {'─'*40}")
for name, typ, notes in RESPONSE_PARAMS:
    print(f"    {name:28s} {typ:20s} {notes}")

print(f"\n  {CYAN}[Tool calling format]{NC}")
print(f"    format:                          {TOOL_CALLING['format']}")
print(f"    tool_choice:                     {TOOL_CALLING['tool_choice']}")
print(f"    tool_call_id_format:             {TOOL_CALLING['tool_call_id_format']}")
print(f"    arguments_format:                {TOOL_CALLING['arguments_format']}")
print(f"    parallel_tool_calls:             {TOOL_CALLING['parallel_tool_calls']}")
print(f"    message_shape: {TOOL_CALLING['message_shape']}")
print(f"    tool_message:  {TOOL_CALLING['tool_message']}")
print(f"    built_in_tools (Responses API):  {TOOL_CALLING['built_in_tools_responses_api_only']}")
print(f"    doc_ref:                         {TOOL_CALLING['doc_ref']}")

# ───────────────────────────────────────────────────────────────────────────
# Section 3 — Live /v1/models response
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 3. Live /v1/models response ──{NC}")
print(f"  Total models:           {len(models)}")
print(f"  Top-level keys:         {list(d.keys())}")
if models:
    sample = models[0]
    print(f"  Sample model object:    {sample.get('id', '?')}")
    print(f"  Sample card keys:       {list(sample.keys())}")

# ───────────────────────────────────────────────────────────────────────────
# Section 4 — Card field availability
# OpenAI /v1/models returns MINIMAL cards — only id/object/created/owned_by.
# No context_length, no pricing, no capabilities. All rich fields are ○ missing.
# Check for shutdown_date field (R07.03 deprecation signal).
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 4. Card field availability (live models) ──{NC}")
# Standard OpenAI fields (the only 4 that /v1/models actually returns):
STANDARD_FIELDS = ["id", "object", "created", "owned_by"]
# Rich fields the catalog/probe would LIKE to see but OpenAI does NOT expose:
RICH_FIELDS = [
    "context_length", "max_completion_tokens", "max_output_tokens",
    "capabilities", "pricing", "size", "family", "tier",
    "free_tier_eligible", "shutdown_date", "description", "top_provider",
]
all_fields = STANDARD_FIELDS + RICH_FIELDS
print(f"  {'Field':30s} {'Available':12s} {'Count':12s}")
print(f"  {'─'*30} {'─'*12} {'─'*12}")
for field in all_fields:
    n = sum(1 for m in models if field in m)
    if n == 0:
        status = f"{YELLOW}○ missing{NC}"
    elif n == len(models):
        status = f"{GREEN}✓ all{NC}"
    else:
        status = f"{YELLOW}~ partial{NC}"
    print(f"  {field:30s} {n}/{len(models):<10d}  {status}")

SIZE_AVAILABLE = any(m.get("size") for m in models)
PRICING_AVAILABLE = any("pricing" in m for m in models)
CTX_AVAILABLE = any("context_length" in m for m in models)
MAXTOK_AVAILABLE = any(m.get("max_completion_tokens") or m.get("max_output_tokens") for m in models)
SHUTDOWN_AVAILABLE = any("shutdown_date" in m for m in models)
print(f"\n  Availability summary (for per-model detail):")
print(f"    Model size available:         {'yes' if SIZE_AVAILABLE else 'no'}  ({sum(1 for m in models if m.get('size'))} models)")
print(f"    Pricing available:            {'yes' if PRICING_AVAILABLE else 'no'}  (catalog is source of truth)")
print(f"    context_length available:     {'yes' if CTX_AVAILABLE else 'no'}  (catalog is source of truth)")
print(f"    max_completion_tokens avail:  {'yes' if MAXTOK_AVAILABLE else 'no'}  (catalog is source of truth)")
print(f"    shutdown_date (R07.03):       {'yes' if SHUTDOWN_AVAILABLE else 'no'}  (deprecation signal)")

# ───────────────────────────────────────────────────────────────────────────
# Section 5 — Per-model detail
# Mirror of _is_free_model() in openai.py:
# OPENAI_FREE_MODEL_WHITELIST is empty (R07.03). OpenAI has NO genuinely free
# ($0/token) models. free_tier_eligible is a separate catalog flag for
# low-cost models covered by monthly credit — NOT the same as is_free.
# Catalog is the source of truth for ctx/max_tok/pricing (live API doesn't expose).
# ───────────────────────────────────────────────────────────────────────────
CATALOG = {
    "gpt-6-astra":    {"ctx": 400000, "max_tok": 65536, "in": 10.00, "out": 50.00, "family": "gpt-6", "tier": "flagship", "free_tier_eligible": False},
    "gpt-6-sol":      {"ctx": 400000, "max_tok": 65536, "in": 2.00, "out": 10.00, "family": "gpt-6", "tier": "standard", "free_tier_eligible": False},
    "gpt-6-luna":     {"ctx": 400000, "max_tok": 65536, "in": 0.10, "out": 0.50, "family": "gpt-6", "tier": "lite", "free_tier_eligible": True},
    "gpt-5.6-sol":    {"ctx": 200000, "max_tok": 65536, "in": 4.00, "out": 20.00, "family": "gpt-5.6", "tier": "daybreak", "free_tier_eligible": False},
    "gpt-5.5":        {"ctx": 200000, "max_tok": 65536, "in": None, "out": None, "family": "gpt-5", "tier": "standard", "free_tier_eligible": False},
    "gpt-5.4":        {"ctx": 128000, "max_tok": 16384, "in": None, "out": None, "family": "gpt-5", "tier": "standard", "free_tier_eligible": False},
    "gpt-5.3-codex":  {"ctx": 200000, "max_tok": 65536, "in": 1.75, "out": 14.00, "family": "gpt-5", "tier": "codex", "free_tier_eligible": False},
    "gpt-4o":         {"ctx": 128000, "max_tok": 16384, "in": None, "out": None, "family": "gpt-4o", "tier": "standard", "free_tier_eligible": False},
    "gpt-4o-mini":    {"ctx": 128000, "max_tok": 16384, "in": None, "out": None, "family": "gpt-4o", "tier": "mini", "free_tier_eligible": True},
    "gpt-4.1-mini":   {"ctx": 1000000, "max_tok": 32768, "in": None, "out": None, "family": "gpt-4.1", "tier": "mini", "free_tier_eligible": True},
    "gpt-4.1":        {"ctx": 1047576, "max_tok": 32768, "in": None, "out": None, "family": "gpt-4.1", "tier": "standard", "free_tier_eligible": False},
    "gpt-4.1-nano":   {"ctx": 1047576, "max_tok": 32768, "in": None, "out": None, "family": "gpt-4.1", "tier": "nano", "free_tier_eligible": False},
    "chat-latest":    {"ctx": 128000, "max_tok": 16384, "in": 5.00, "out": 30.00, "family": "chatgpt", "tier": "chat-latest", "free_tier_eligible": False},
    "gpt-realtime-2.1": {"ctx": 128000, "max_tok": None, "in": None, "out": None, "family": "realtime", "tier": "standard", "free_tier_eligible": False},
    "gpt-realtime-2.1-mini": {"ctx": 128000, "max_tok": None, "in": None, "out": None, "family": "realtime", "tier": "mini", "free_tier_eligible": True},
    "o1":             {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "o-series", "tier": "flagship", "free_tier_eligible": False},
    "o3":             {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "o-series", "tier": "flagship", "free_tier_eligible": False},
    "o3-mini":        {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "o-series", "tier": "mini", "free_tier_eligible": False},
    "o4-mini":        {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "o-series", "tier": "mini", "free_tier_eligible": False},
    "gpt-5":          {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "gpt-5", "tier": "flagship", "free_tier_eligible": False},
    "gpt-5-mini":     {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "gpt-5", "tier": "mini", "free_tier_eligible": False},
    "gpt-5-nano":     {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "gpt-5", "tier": "nano", "free_tier_eligible": False},
    "gpt-5-pro":      {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "gpt-5", "tier": "pro", "free_tier_eligible": False},
    "gpt-5-codex":    {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "gpt-5", "tier": "codex", "free_tier_eligible": False},
    "gpt-5.1":        {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "gpt-5", "tier": "standard", "free_tier_eligible": False},
    "gpt-5.1-codex":  {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "gpt-5", "tier": "codex", "free_tier_eligible": False},
    "gpt-5.2":        {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "gpt-5", "tier": "standard", "free_tier_eligible": False},
    "gpt-5.2-pro":    {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "gpt-5", "tier": "pro", "free_tier_eligible": False},
    "gpt-5.4-mini":   {"ctx": 128000, "max_tok": 16384, "in": None, "out": None, "family": "gpt-5", "tier": "mini", "free_tier_eligible": False},
    "gpt-5.4-nano":   {"ctx": 128000, "max_tok": 16384, "in": None, "out": None, "family": "gpt-5", "tier": "nano", "free_tier_eligible": False},
    "gpt-5.4-pro":    {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "gpt-5", "tier": "pro", "free_tier_eligible": False},
    "gpt-5.5-pro":    {"ctx": 200000, "max_tok": 100000, "in": None, "out": None, "family": "gpt-5", "tier": "pro", "free_tier_eligible": False},
    "gpt-5.6-luna":   {"ctx": 200000, "max_tok": 65536, "in": None, "out": None, "family": "gpt-5.6", "tier": "daybreak", "free_tier_eligible": False},
    "gpt-5.6-terra":  {"ctx": 200000, "max_tok": 65536, "in": None, "out": None, "family": "gpt-5.6", "tier": "daybreak", "free_tier_eligible": False},
    "gpt-3.5-turbo":  {"ctx": 16384, "max_tok": 4096, "in": None, "out": None, "family": "gpt-3.5", "tier": "legacy", "free_tier_eligible": False},
    "gpt-3.5-turbo-16k": {"ctx": 16384, "max_tok": 4096, "in": None, "out": None, "family": "gpt-3.5", "tier": "legacy", "free_tier_eligible": False},
}

# Mirror of _is_free_model() in openai.py:
# OPENAI_FREE_MODEL_WHITELIST is empty (R07.03). OpenAI has NO genuinely free
# ($0/token) models. free_tier_eligible is a separate catalog flag for
# low-cost models covered by monthly credit — NOT the same as is_free.
OPENAI_FREE_MODEL_WHITELIST: frozenset[str] = frozenset()
def is_free(model_id: str) -> bool:
    """Mirror of _is_free_model() in openai.py — always False since R07.03."""
    return model_id in OPENAI_FREE_MODEL_WHITELIST  # always False since R07.03

def catalog_key(live_id: str) -> str:
    """Map live model id to catalog key form.
       OpenAI model ids have NO date suffixes (unlike Mistral),
       so catalog_key is identity — no normalization needed."""
    return live_id

def catalog_lookup(live_id: str):
    """Identity lookup — OpenAI ids already match catalog keys exactly."""
    if live_id in CATALOG:
        return live_id, CATALOG[live_id]
    return None, None

def family_of(model_id: str) -> str:
    mid = model_id.lower()
    if mid.startswith("gpt-6-"):                              return "gpt-6"
    if mid.startswith("gpt-5.6"):                            return "gpt-5.6"
    if mid.startswith("gpt-5.5"):                            return "gpt-5.5"
    if mid.startswith("gpt-5.4"):                            return "gpt-5.4"
    if mid.startswith("gpt-5.3"):                            return "gpt-5.3"
    if mid.startswith("gpt-5.2"):                            return "gpt-5.2"
    if mid.startswith("gpt-5.1"):                            return "gpt-5.1"
    if mid.startswith("gpt-5-") or mid == "gpt-5":           return "gpt-5"
    if mid.startswith("gpt-4.1"):                            return "gpt-4.1"
    if mid.startswith("gpt-4o"):                             return "gpt-4o"
    if mid.startswith("gpt-3.5"):                            return "gpt-3.5"
    if mid.startswith(("o1", "o3", "o4")):                   return "o-series"
    if mid.startswith("chat-"):                              return "chatgpt"
    if mid.startswith("gpt-realtime"):                      return "realtime"
    # Catalog fallback
    _, meta = catalog_lookup(model_id)
    if meta:
        return meta.get("family", "other")
    return "other"

def get_tier(model_id: str) -> str:
    _, meta = catalog_lookup(model_id)
    return meta.get("tier", "—") if meta else "—"

def get_ctx(model_id: str, m_card: dict) -> str:
    live = m_card.get("context_length")
    if live:
        return str(live)
    _, meta = catalog_lookup(model_id)
    if meta and meta.get("ctx"):
        return f"{meta['ctx']}(cat)"  # from catalog
    return "—"

def get_max_tok(model_id: str) -> str:
    _, meta = catalog_lookup(model_id)
    if meta and meta.get("max_tok"):
        return f"{meta['max_tok']}(cat)"
    return "—"

def get_size(m_card: dict) -> str:
    # OpenAI /v1/models does NOT expose model size — always "—"
    return "—"

def get_pricing(model_id: str) -> str:
    _, meta = catalog_lookup(model_id)
    if not meta:
        return "—"
    if meta["in"] is None or meta["out"] is None:
        return "—"
    if meta["in"] == 0.0 and meta["out"] == 0.0:
        return "$0/$0(cat)"
    return f"${meta['in']}/${meta['out']}(cat)"

def get_free_tier_eligible(model_id: str) -> bool:
    """Catalog flag — low-cost models covered by monthly credit.
       DISTINCT from is_free (which is always False since R07.03)."""
    _, meta = catalog_lookup(model_id)
    if not meta:
        return False
    return bool(meta.get("free_tier_eligible", False))

# Non-chat filter (mirrors _NON_CHAT_PATTERNS in openai.py)
NON_CHAT = ("embedding", "tts", "transcribe", "whisper", "image", "sora", "moderation", "babbage", "davinci")
chat_models = [m for m in models if not any(p in m.get("id", "").lower() for p in NON_CHAT)]
non_chat_models = [m for m in models if any(p in m.get("id", "").lower() for p in NON_CHAT)]

print(f"\n{CYAN}── 5. Per-model detail ──{NC}")
print(f"  (Free rule: model_id ∈ OPENAI_FREE_MODEL_WHITELIST — EMPTY since R07.03 → is_free always False)")
print(f"  (free_tier_eligible is a SEPARATE catalog flag for low-cost models — NOT the same as is_free)")
print(f"  (Live API exposes only id/object/created/owned_by — all rich fields come from catalog '(cat)')")
print(f"  (Chat-vs-non-chat filter via NON_CHAT tuple: {len(NON_CHAT)} patterns)")
print(f"")
print(f"  Chat-capable models (after NON_CHAT filter):  {len(chat_models)}")
print(f"  Non-chat models filtered:                      {len(non_chat_models)}")
print(f"")
print(f"  {'Model':28s} {'Family':12s} {'Tier':10s} {'Ctx':12s} {'MaxTok':12s} {'Size':6s} {'Pricing':16s} {'Free':5s} {'FreeTier':9s} {'Paid':5s}")
print(f"  {'─'*28} {'─'*12} {'─'*10} {'─'*12} {'─'*12} {'─'*6} {'─'*16} {'─'*5} {'─'*9} {'─'*5}")
for m in sorted(chat_models, key=lambda x: x.get("id", "")):
    mid = m.get("id", "?")
    fam = family_of(mid)
    tier = get_tier(mid)
    ctx = get_ctx(mid, m)
    max_tok = get_max_tok(mid)
    size = get_size(m)
    pricing = get_pricing(mid)
    free = is_free(mid)
    fte = get_free_tier_eligible(mid)
    paid = not free
    free_s = f"{GREEN}yes{NC}" if free else "no"
    fte_s = f"{GREEN}yes{NC}" if fte else "no"
    paid_s = "yes" if paid else f"{GREEN}—{NC}"
    print(f"  {mid:28s} {fam:12s} {tier:10s} {ctx:12s} {max_tok:12s} {size:6s} {pricing:16s} {free_s:5s} {fte_s:9s} {paid_s:5s}")

# ───────────────────────────────────────────────────────────────────────────
# Section 6 — Free vs full catalog
# ───────────────────────────────────────────────────────────────────────────
free_models = [m.get("id", "?") for m in chat_models if is_free(m.get("id", ""))]
paid_models = [m.get("id", "?") for m in chat_models if not is_free(m.get("id", ""))]
free_tier_eligible_models = [m.get("id", "?") for m in chat_models if get_free_tier_eligible(m.get("id", ""))]

print(f"\n{CYAN}── 6. Free vs full catalog ──{NC}")
print(f"  Live total (chat-filtered):                  {len(chat_models)}")
print(f"  Free (is_free=yes):                          {len(free_models)}")
if not free_models:
    print(f"    (none — OPENAI_FREE_MODEL_WHITELIST is empty since R07.03; OpenAI has NO $0/token models)")
else:
    for m in free_models:
        print(f"    {GREEN}✓{NC} {m}")
print(f"  Free-tier-eligible (low-cost credit, NOT is_free):  {len(free_tier_eligible_models)}")
for m in free_tier_eligible_models:
    print(f"    {GREEN}✓{NC} {m}")
if not free_tier_eligible_models:
    print(f"    (none in live API; catalog pins 4: gpt-6-luna, gpt-4o-mini, gpt-4.1-mini, gpt-realtime-2.1-mini)")
print(f"  Paid (is_paid=yes):                          {len(paid_models)}")
if paid_models:
    for m in paid_models[:5]:
        print(f"    · {m}")
    if len(paid_models) > 5:
        print(f"    ... + {len(paid_models) - 5} more")

# Non-chat breakdown
if non_chat_models:
    print(f"\n  Non-chat models filtered out ({len(non_chat_models)}):")
    nc_patterns = Counter()
    for m in non_chat_models:
        for p in NON_CHAT:
            if p in m.get("id", "").lower():
                nc_patterns[p] += 1
                break
    for p, c in sorted(nc_patterns.items()):
        print(f"    {p:15s} → {c} models")

# Catalog drift — identity catalog_key (no date normalization needed for OpenAI)
live_ids = {m["id"] for m in chat_models if m.get("id")}
in_live_not_catalog = sorted(live_ids - set(CATALOG.keys()))
in_catalog_not_live = sorted(set(CATALOG.keys()) - live_ids)

print(f"\n  Static OPENAI_MODELS catalog size:  {len(CATALOG)}")
print(f"  In live API but NOT in catalog:     {len(in_live_not_catalog)}")
for m in in_live_not_catalog[:20]:
    print(f"    + {m}")
if len(in_live_not_catalog) > 20:
    print(f"    ... + {len(in_live_not_catalog) - 20} more")
print(f"  In catalog but NOT in live API:     {len(in_catalog_not_live)}")
for m in in_catalog_not_live:
    print(f"    - {m}")

# Catalog free-tier-eligible subset (the 4 entries pinned in CATALOG)
catalog_free_tier = sorted(k for k in CATALOG if CATALOG[k].get("free_tier_eligible"))
catalog_paid = sorted(k for k in CATALOG if not CATALOG[k].get("free_tier_eligible"))
print(f"\n  Catalog free-tier-eligible subset:  {len(catalog_free_tier)}")
for m in catalog_free_tier:
    print(f"    {GREEN}✓{NC} {m}")
print(f"  Catalog paid (non-credit) subset:   {len(catalog_paid)}")

# Deprecation early-warning — check live cards for shutdown_date (R07.03 feature)
deprecated = [m for m in models if m.get("shutdown_date")]
if deprecated:
    print(f"\n  {YELLOW}⚠ Deprecation warning — {len(deprecated)} live models carry a 'shutdown_date' field:{NC}")
    for m in deprecated:
        print(f"    ⚠ {m.get('id', '?'):40s}  shutdown_date={m.get('shutdown_date')}")
else:
    print(f"\n  No 'shutdown_date' fields present in live cards (R07.03 deprecation signal not yet emitted).")

print(f"\n{CYAN}{'─'*78}{NC}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_openai.json")
print(f"{CYAN}{'─'*78}{NC}")
PYEOF
