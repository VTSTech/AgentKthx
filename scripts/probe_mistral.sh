#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_mistral.sh — Validate Mistral API Technical Reference (expanded)
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls, no tokens burned).
# Requires: MISTRAL_API_KEY in env  (https://console.mistral.ai/api-keys)
# Usage:    bash probe_mistral.sh
# Output:   /tmp/agentkthx_probe_mistral.json  (raw API response)
#
# Report sections:
#   1. Endpoint           — base URL, auth shape, HTTP status, raw JSON path
#   2. Static API surface  — request params, response shape, tool calling
#                            format (sourced from docs/api/MISTRAL_API_
#                            TECHNICAL_REFERENCE.md; hardcoded — slow-moving)
#   3. Live /v1/models     — top-level keys, total count, sample card
#   4. Card field avail.   — which fields each live model exposes
#   5. Per-model detail     — id, family, max context, max tokens, size avail,
#                            pricing avail, is_free, is_paid
#   6. Free vs full catalog — free subset, paid subset, static catalog drift
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

API_KEY="${MISTRAL_API_KEY:-}"
[ -z "$API_KEY" ] && echo "ERROR: Set MISTRAL_API_KEY first: export MISTRAL_API_KEY=..." >&2 && exit 1

BASE_URL="${MISTRAL_BASE_URL:-https://api.mistral.ai/v1}"

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Endpoint
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}${BOLD}═══ Mistral La Plateforme API Probe ═══${NC}"
echo ""
echo -e "${CYAN}── 1. Endpoint ──${NC}"
echo "  Base URL:     ${BASE_URL}"
echo "  Models path:  /models"
echo "  Auth:         Bearer \$MISTRAL_API_KEY (len=${#API_KEY})"
echo "  Request:      GET ${BASE_URL}/models"

HTTP_CODE=$(curl -s -m 15 -o /tmp/agentkthx_probe_mistral.json -w "%{http_code}" \
    -H "Authorization: Bearer $API_KEY" \
    -H "User-Agent: AgentKthx-probe/0.x" \
    -H "Accept: application/json" \
    "${BASE_URL}/models")

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "  ${RED}✗ HTTP ${HTTP_CODE}${NC}"
    echo "  Response body (first 600 bytes):"
    head -c 600 /tmp/agentkthx_probe_mistral.json
    echo
    exit 1
fi
echo -e "  ${GREEN}✓ HTTP 200${NC}  (raw JSON: /tmp/agentkthx_probe_mistral.json)"

python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_mistral.json')); assert isinstance(d.get('data'), list)" 2>/dev/null || {
    echo -e "  ${RED}✗ Response is not OpenAI-shaped {\"data\": [...]}.${NC}"
    head -c 600 /tmp/agentkthx_probe_mistral.json
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

d = json.load(open("/tmp/agentkthx_probe_mistral.json"))
models = d.get("data", [])

# ───────────────────────────────────────────────────────────────────────────
# Section 2 — Static API surface (sourced from docs/api/MISTRAL_API_TECHNICAL_REFERENCE.md)
# Hardcoded in the probe because the doc is slow-moving and parsing it inline
# would be brittle. Cite the doc so the source of truth is visible.
# ───────────────────────────────────────────────────────────────────────────
REQUEST_PARAMS = [
    # (name, type, required, default, notes)
    ("model",              "string",            True,  None,    "Model id from /v1/models"),
    ("messages",           "array[Message]",    True,  None,    "Chat history; roles: system/user/assistant/tool"),
    ("temperature",        "float",             False, "0.7",   "Sampling temperature; default_model_temperature per card"),
    ("max_tokens",         "int",               False, "8192",  "Output cap; AgentKthx default_max_tokens"),
    ("top_p",              "float",             False, "1.0",   "Nucleus sampling"),
    ("random_seed",        "int",               False, None,    "Mistral's name for `seed` (wire delta vs OpenAI)"),
    ("stop",               "string | array",    False, None,    "Stop sequences"),
    ("n",                  "int",               False, "1",     "Completions; not supported by mistral-large-2512"),
    ("stream",             "bool",              False, "false", "SSE stream"),
    ("safe_prompt",        "bool",              False, "false", "Injects Mistral safety system prompt"),
    ("service_tier",       "string",            False, None,    "'auto' | 'standard_only' | omit"),
    ("prompt_cache_key",   "string",            False, None,    "Cached-prefix billing at 10% of input; from session_id"),
    ("response_format",    "object",            False, None,    "{'type':'text'|'json_object'|'json_schema','schema':{...}}"),
    ("tools",              "array[Tool]",       False, None,    "Function-calling tool schemas"),
    ("tool_choice",        "string | object",   False, "auto",  "'auto'|'none'|'any'|'required'|{'type':'function',...}"),
    ("parallel_tool_calls","bool",              False, "true",  "Allow parallel tool calls in one assistant turn"),
    ("reasoning_effort",   "string",            False, None,    "'none'|'minimal'|'low'|'medium'|'high'|'xhigh' (xhigh is Mistral-only)"),
    ("frequency_penalty",  "float",             False, "0.0",   ""),
    ("presence_penalty",   "float",             False, "0.0",   ""),
    ("metadata",           "map",               False, None,    "Free-form key/value; correlate in Studio usage dashboard"),
    # OpenAI-only kwargs stripped by MistralBackend._build_request:
    #   seed, logprobs, top_k, user, max_completion_tokens
]

RESPONSE_PARAMS = [
    # (name, type, notes)
    ("id",                 "string",            "Request id (opaque)"),
    ("object",             "string",            "'chat.completion'"),
    ("created",            "int (unix ts)",     "Response timestamp"),
    ("model",              "string",            "Model id used"),
    ("choices",            "array[Choice]",     "One per n; each has index/message/finish_reason/logprobs"),
    ("choices[].message",  "object",            "{role:'assistant', content, tool_calls?, prefix?}"),
    ("choices[].finish_reason", "string",       "'stop'|'length'|'model_length'|'error'|'tool_calls' (model_length is Mistral-only)"),
    ("usage",              "object",            "{prompt_tokens, completion_tokens, total_tokens}"),
]

TOOL_CALLING = {
    "format": "OpenAI-style tools[] array (function-calling convention)",
    "wire_deltas": [
        "tool_call.id is short opaque (e.g. 'D681PevKs'), NO 'call_' prefix",
        "function_call.arguments may arrive as JSON string OR parsed object (both accepted)",
        "tool_choice='required' accepted as alias for 'any' (Mistral-native)",
        "Mistral-hosted tool types beyond 'function': document(s) URL fetch + code_interpreter",
        "parallel_tool_calls (bool) controls single vs multi-tool per turn",
    ],
    "message_shape": (
        '{"role":"assistant","content":"...","tool_calls":[{"id":"D681PevKs",'
        '"type":"function","function":{"name":"get_weather","arguments":"{\\"city\\":\\"NYC\\"}"}}]}'
    ),
    "tool_message": (
        '{"role":"tool","content":"result","tool_call_id":"D681PevKs","name":"get_weather"}'
    ),
    "doc_ref": "docs/api/MISTRAL_API_TECHNICAL_REFERENCE.md §Function Calling Implementation",
}

print(f"\n{CYAN}── 2. Static API surface{NC}  (from docs/api/MISTRAL_API_TECHNICAL_REFERENCE.md)")
print(f"  {CYAN}[Request params — POST /chat/completions body]{NC}")
print(f"    {'Name':24s} {'Type':22s} {'Req':4s} {'Default':14s} Notes")
print(f"    {'─'*24} {'─'*22} {'─'*4} {'─'*14} {'─'*40}")
for name, typ, req, default, notes in REQUEST_PARAMS:
    req_s = "yes" if req else "no"
    default_s = str(default) if default is not None else "—"
    print(f"    {name:24s} {typ:22s} {req_s:4s} {default_s:14s} {notes}")

print(f"\n  {CYAN}[Response shape — chat.completion object]{NC}")
print(f"    {'Field':28s} {'Type':20s} Notes")
print(f"    {'─'*28} {'─'*20} {'─'*40}")
for name, typ, notes in RESPONSE_PARAMS:
    print(f"    {name:28s} {typ:20s} {notes}")

print(f"\n  {CYAN}[Tool calling format]{NC}")
print(f"    format:        {TOOL_CALLING['format']}")
print(f"    message_shape: {TOOL_CALLING['message_shape']}")
print(f"    tool_message:  {TOOL_CALLING['tool_message']}")
print(f"    wire_deltas:")
for d_item in TOOL_CALLING['wire_deltas']:
    print(f"      - {d_item}")
print(f"    doc_ref:       {TOOL_CALLING['doc_ref']}")

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
# Section 4 — Card field availability (which fields each live model exposes)
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 4. Card field availability (live models) ──{NC}")
# Standard OpenAI fields:
STANDARD_FIELDS = ["id", "object", "created", "owned_by"]
# Rich Mistral-specific fields:
RICH_FIELDS = [
    "capabilities", "max_context_length", "default_model_temperature",
    "aliases", "deprecation", "description", "name",
    "pricing", "size", "max_tokens", "max_output_tokens",
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
    # ANSI codes make string longer than visual width; print raw status
    print(f"  {field:30s} {n}/{len(models):<10d}  {status}")

# Report on what's available vs missing for the per-model detail
SIZE_AVAILABLE = any(m.get("size") for m in models)
PRICING_AVAILABLE = any("pricing" in m for m in models)
MAXCTX_AVAILABLE = any(m.get("max_context_length") for m in models)
MAXTOK_AVAILABLE = any(m.get("max_tokens") or m.get("max_output_tokens") for m in models)
print(f"\n  Availability summary (for per-model detail):")
print(f"    Model size available:        {'yes' if SIZE_AVAILABLE else 'no'}  ({sum(1 for m in models if m.get('size'))} models)")
print(f"    Pricing available:           {'yes' if PRICING_AVAILABLE else 'no'}  (catalog is source of truth)")
print(f"    Max context available:        {'yes' if MAXCTX_AVAILABLE else 'no'}  (catalog is source of truth)")
print(f"    Max tokens available:         {'yes' if MAXTOK_AVAILABLE else 'no'}  (default_max_tokens from catalog)")

# ───────────────────────────────────────────────────────────────────────────
# Section 5 — Per-model detail
# Free-tier rule mirrors _is_free_model() in agentkthx/plugins/mistral/mistral.py:
#   labs- prefix OR catalog pricing.input==0 AND pricing.output==0
# Catalog is the source of truth for pricing (live API doesn't expose it).
# ───────────────────────────────────────────────────────────────────────────
CATALOG = {
    "mistral-medium-latest":    {"ctx": 262144, "max_tok": 8192, "in": 2.00, "out": 6.00, "family": "mistral-medium"},
    "mistral-medium-3-5":       {"ctx": 262144, "max_tok": 8192, "in": 2.00, "out": 6.00, "family": "mistral-medium"},
    "mistral-medium-3":         {"ctx": 262144, "max_tok": 8192, "in": 2.00, "out": 6.00, "family": "mistral-medium"},
    "mistral-small-latest":     {"ctx": 262144, "max_tok": 8192, "in": 0.20, "out": 0.50, "family": "mistral-small"},
    "mistral-small-4":          {"ctx": 262144, "max_tok": 8192, "in": 0.20, "out": 0.50, "family": "mistral-small"},
    "mistral-large-latest":    {"ctx": 262144, "max_tok": 8192, "in": 0.50, "out": 1.50, "family": "mistral-large"},
    "mistral-large-3":          {"ctx": 262144, "max_tok": 8192, "in": 0.50, "out": 1.50, "family": "mistral-large"},
    "ministral-14b-latest":     {"ctx": 262144, "max_tok": 8192, "in": 0.20, "out": 0.50, "family": "ministral"},
    "ministral-8b-latest":      {"ctx": 262144, "max_tok": 8192, "in": 0.10, "out": 0.30, "family": "ministral"},
    "ministral-3b-latest":      {"ctx": 262144, "max_tok": 8192, "in": 0.04, "out": 0.04, "family": "ministral"},
    "devstral-latest":          {"ctx": 262144, "max_tok": 8192, "in": 0.20, "out": 0.50, "family": "devstral"},
    "magistral-medium-latest":  {"ctx": 131072, "max_tok": 8192, "in": 2.00, "out": 6.00, "family": "magistral"},
    "magistral-small-latest":   {"ctx": 131072, "max_tok": 8192, "in": 0.50, "out": 1.50, "family": "magistral"},
    "codestral-latest":         {"ctx": 131072, "max_tok": 8192, "in": 0.20, "out": 0.60, "family": "codestral"},
    "labs-mistral-small-creative": {"ctx": 262144, "max_tok": 8192, "in": 0.0, "out": 0.0, "family": "labs"},
}

def catalog_key(live_id: str) -> str:
    """Map live model id to catalog key form (strip date suffixes, normalize legacy aliases)."""
    lid = live_id.lower()
    lid = re.sub(r"-\d{4}$", "", lid)
    lid = lid.replace("open-mistral-", "mistral-").replace("open-mixtral-", "mistral-")
    return lid

def catalog_lookup(live_id: str):
    """Try the catalog with progressively looser key matches:
       1. catalog_key(live_id) as-is
       2. catalog_key(live_id) + '-latest' fallback
       Returns (catalog_key_or_None, catalog_meta_or_None)."""
    k = catalog_key(live_id)
    if k in CATALOG:
        return k, CATALOG[k]
    # Try +'-latest' fallback so 'magistral-small-2411' (-> 'magistral-small')
    # matches catalog key 'magistral-small-latest'.
    k2 = k + "-latest"
    if k2 in CATALOG:
        return k2, CATALOG[k2]
    return None, None

def is_free(model_id: str) -> bool:
    """Mirror of _is_free_model() in mistral.py."""
    key = catalog_key(model_id)
    if key.startswith("labs-"):
        return True
    _, meta = catalog_lookup(model_id)
    if not meta:
        return False
    return meta["in"] == 0.0 and meta["out"] == 0.0

def family_of(model_id: str) -> str:
    mid = model_id.lower()
    if mid.startswith("labs-"):                  return "labs"
    if mid.startswith("magistral-"):             return "magistral"
    if mid.startswith("devstral"):               return "devstral"
    if mid.startswith("codestral"):              return "codestral"
    if mid.startswith("ministral-"):             return "ministral"
    if mid.startswith("mistral-medium"):         return "mistral-medium"
    if mid.startswith("mistral-small"):          return "mistral-small"
    if mid.startswith("mistral-large"):          return "mistral-large"
    if mid.startswith("mistral-"):               return "mistral-other"
    if "embed" in mid:                            return "embed"
    if "ocr" in mid or "pixtral" in mid:         return "vision/ocr"
    return "other"

def get_ctx(model_id: str, m_card: dict) -> str:
    live = m_card.get("max_context_length")
    if live:  return str(live)
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
    s = m_card.get("size")
    return str(s) if s else "—"

def get_pricing(model_id: str) -> str:
    _, meta = catalog_lookup(model_id)
    if not meta:  return "—"
    if meta["in"] == 0.0 and meta["out"] == 0.0:  return "$0/$0(cat)"
    return f"${meta['in']}/${meta['out']}(cat)"

print(f"\n{CYAN}── 5. Per-model detail ──{NC}")
print(f"  (Free rule: labs- prefix OR catalog pricing.input==0 AND pricing.output==0)")
print(f"  Live API does not expose pricing/size/max_tokens — catalog is source of truth (marked '(cat)').")
print(f"")
print(f"  {'Model':40s} {'Family':18s} {'Ctx':12s} {'MaxTok':12s} {'Size':10s} {'Pricing':18s} {'Free':5s} {'Paid':5s}")
print(f"  {'─'*40} {'─'*18} {'─'*12} {'─'*12} {'─'*10} {'─'*18} {'─'*5} {'─'*5}")
for m in sorted(models, key=lambda x: x.get("id", "")):
    mid = m.get("id", "?")
    fam = family_of(mid)
    ctx = get_ctx(mid, m)
    max_tok = get_max_tok(mid)
    size = get_size(m)
    pricing = get_pricing(mid)
    free = is_free(mid)
    paid = not free
    free_s = f"{GREEN}yes{NC}" if free else "no"
    paid_s = "yes" if paid else f"{GREEN}—{NC}"
    print(f"  {mid:40s} {fam:18s} {ctx:12s} {max_tok:12s} {size:10s} {pricing:18s} {free_s:5s} {paid_s:5s}")

# ───────────────────────────────────────────────────────────────────────────
# Section 6 — Free vs full catalog
# ───────────────────────────────────────────────────────────────────────────
free_models = [m.get("id", "?") for m in models if is_free(m.get("id", ""))]
paid_models = [m.get("id", "?") for m in models if not is_free(m.get("id", ""))]

print(f"\n{CYAN}── 6. Free vs full catalog ──{NC}")
print(f"  Live total:              {len(models)}")
print(f"  Free (is_free=yes):      {len(free_models)}")
for m in free_models:
    print(f"    {GREEN}✓{NC} {m}")
if not free_models:
    print(f"    (none — no labs-* models surfaced; catalog has 'labs-mistral-small-creative' pinned)")
print(f"  Paid (is_paid=yes):      {len(paid_models)}")
if paid_models:
    # Show first few paid for sanity, suppress the rest
    for m in paid_models[:5]:
        print(f"    · {m}")
    if len(paid_models) > 5:
        print(f"    ... + {len(paid_models) - 5} more")

# Catalog drift (with date-version stripping so aliases don't mask drift)
# For drift detection: a live id 'matches' the catalog if catalog_lookup()
# resolves it (either direct, date-stripped, or +'-latest' fallback).
matched_keys = set()
for m in models:
    if not m.get("id"):
        continue
    k, _ = catalog_lookup(m["id"])
    if k:
        matched_keys.add(k)
# unmatched live ids (drift candidates) — for reporting only
unmatched_live_ids = sorted({
    m.get("id", "") for m in models
    if m.get("id") and not catalog_lookup(m["id"])[0]
})
in_live_not_catalog = unmatched_live_ids
in_catalog_not_live = sorted(set(CATALOG.keys()) - matched_keys)

print(f"\n  Static MISTRAL_MODELS catalog size:  {len(CATALOG)}")
print(f"  In live API but NOT in catalog:      {len(in_live_not_catalog)}")
for m in in_live_not_catalog[:20]:
    print(f"    + {m}")
if len(in_live_not_catalog) > 20:
    print(f"    ... + {len(in_live_not_catalog) - 20} more")
print(f"  In catalog but NOT in live API:      {len(in_catalog_not_live)}")
for m in in_catalog_not_live:
    print(f"    - {m}")

# Catalog free subset (catalog entries where is_free returns True)
catalog_free = sorted(k for k in CATALOG if is_free(k))
catalog_paid = sorted(k for k in CATALOG if not is_free(k))
print(f"\n  Catalog free subset:                  {len(catalog_free)}")
for m in catalog_free:
    print(f"    {GREEN}✓{NC} {m}")
print(f"  Catalog paid subset:                 {len(catalog_paid)}")

# Deprecation field early-warning (unique to Mistral)
deprecated = [m for m in models if m.get("deprecation")]
if deprecated:
    print(f"\n  {YELLOW}⚠ Deprecation warning — {len(deprecated)} live models carry a 'deprecation' field:{NC}")
    for m in deprecated:
        print(f"    ⚠ {m.get('id', '?'):40s}  deprecation={m.get('deprecation')}")

print(f"\n{CYAN}{'─'*78}{NC}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_mistral.json")
print(f"{CYAN}{'─'*78}{NC}")
PYEOF
