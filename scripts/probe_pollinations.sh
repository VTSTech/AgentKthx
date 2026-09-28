#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_pollinations.sh — Validate Pollinations API Technical Reference (expanded)
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls, no pollen burned).
# Requires: POLLINATIONS_API_KEY in env  (OPTIONAL — the ONLY AgentKthx
#             backend that runs keyless; /v1/models is anonymous. A key,
#             when present, scopes the catalog to entitlements (~134 cards
#             vs ~307 anonymous) — the Quest-Pollen-eligible subset.)
# Usage:    bash probe_pollinations.sh
# Output:   /tmp/agentkthx_probe_pollinations.json        (raw /v1/models)
#           /tmp/agentkthx_probe_pollinations_bare.json   (raw bare /models)
#
# Report sections:
#   1. Endpoint           — base URL, auth shape, HTTP status, raw JSON path
#   2. Static API surface  — request params, response shape, tool calling
#                            format (sourced from docs/api/POLLINATIONS_API_
#                            TECHNICAL_REFERENCE.md; hardcoded — slow-moving)
#   3. Live /v1/models     — top-level keys, total count, sample card
#   4. Card field avail.   — which fields each live model exposes
#                            (rich Pollinations cards + bare /models sub-block)
#   5. Per-model detail     — id, family, ctx, max_tok, size, pricing,
#                            is_free, is_paid (chat/text models only)
#   6. Free vs full catalog — three-tier free concept (zero-cost / free TIER
#                            / legacy anon), catalog drift, healthy_fallbacks()
#                            top-3 simulation, deprecation field absence
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

API_KEY="${POLLINATIONS_API_KEY:-}"
# Pollinations is the ONLY AgentKthx backend that runs keyless — anonymous
# /v1/models is public. Do NOT exit 1 on missing key (unlike probe_mistral.sh).

BASE_URL="${POLLINATIONS_BASE_URL:-https://gen.pollinations.ai/v1}"
BARE_URL="https://gen.pollinations.ai"   # bare host (no /v1/) — name-keyed schema

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Endpoint
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}${BOLD}═══ Pollinations API Probe ═══${NC}"
echo ""
echo -e "${CYAN}── 1. Endpoint ──${NC}"
echo "  Base URL:     ${BASE_URL}"
echo "  Bare host:    ${BARE_URL}  (no /v1/ — name-keyed schema, paid_only lives here)"
echo "  Models path:  /models  (anonymous by default; Bearer key scopes to entitlements)"
echo "  Chat path:    /chat/completions"
if [ -n "$API_KEY" ]; then
    echo "  Auth:         Bearer \$POLLINATIONS_API_KEY (len=${#API_KEY}, optional)"
    echo "  Note:         keyed feed scopes catalog to entitlements (~134 vs ~307 anonymous)"
else
    echo "  Auth:         (anonymous — /v1/models is public; keyless IP-rate-limited)"
fi
echo "  Request:      GET ${BASE_URL}/models"

# First curl — /v1/models (OpenAI-shaped, id-keyed). Auth optional.
if [ -n "$API_KEY" ]; then
    HTTP_CODE=$(curl -s -m 15 -o /tmp/agentkthx_probe_pollinations.json -w "%{http_code}" \
        -H "Authorization: Bearer $API_KEY" \
        -H "User-Agent: AgentKthx-probe/0.x" \
        -H "Accept: application/json" \
        "${BASE_URL}/models")
else
    HTTP_CODE=$(curl -s -m 15 -o /tmp/agentkthx_probe_pollinations.json -w "%{http_code}" \
        -H "User-Agent: AgentKthx-probe/0.x" \
        -H "Accept: application/json" \
        "${BASE_URL}/models")
fi

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "  ${RED}✗ HTTP ${HTTP_CODE}${NC}"
    echo "  Response body (first 600 bytes):"
    head -c 600 /tmp/agentkthx_probe_pollinations.json
    echo
    exit 1
fi
echo -e "  ${GREEN}✓ HTTP 200${NC}  (raw JSON: /tmp/agentkthx_probe_pollinations.json)"

python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_pollinations.json')); assert isinstance(d.get('data'), list)" 2>/dev/null || {
    echo -e "  ${RED}✗ Response is not OpenAI-shaped {\"data\": [...]}.${NC}"
    head -c 600 /tmp/agentkthx_probe_pollinations.json
    echo
    exit 1
}

# Second curl — bare /models (name-keyed schema; ONLY feed carrying paid_only)
echo "  Request:      GET ${BARE_URL}/models  (bare, anonymous — paid_only lives here)"
BARE_CODE=$(curl -s -m 15 -o /tmp/agentkthx_probe_pollinations_bare.json -w "%{http_code}" \
    -H "User-Agent: AgentKthx-probe/0.x" \
    -H "Accept: application/json" \
    "${BARE_URL}/models" 2>/dev/null || echo "000")

if [ "$BARE_CODE" != "200" ]; then
    echo -e "  ${YELLOW}⚠ bare /models HTTP ${BARE_CODE} — paid_only discovery will be skipped${NC}"
else
    echo -e "  ${GREEN}✓ HTTP 200${NC}  (raw JSON: /tmp/agentkthx_probe_pollinations_bare.json)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Sections 2–6 — Python analysis (single heredoc, parses JSON + emits report)
# ═══════════════════════════════════════════════════════════════════════════
python3 << 'PYEOF'
import json
import os
from collections import Counter

CYAN = '\033[0;36m'; GREEN = '\033[0;32m'; YELLOW = '\033[0;33m'; RED = '\033[0;31m'; NC = '\033[0m'

d = json.load(open("/tmp/agentkthx_probe_pollinations.json"))
models = d.get("data", [])

# Bare /models feed — name-keyed, schema differs from /v1/models
try:
    bare = json.load(open("/tmp/agentkthx_probe_pollinations_bare.json"))
    if not isinstance(bare, list):
        bare = []
except Exception:
    bare = []

# ───────────────────────────────────────────────────────────────────────────
# Section 2 — Static API surface (sourced from docs/api/POLLINATIONS_API_TECHNICAL_REFERENCE.md)
# Hardcoded in the probe because the doc is slow-moving and parsing it inline
# would be brittle. Cite the doc so the source of truth is visible.
# ───────────────────────────────────────────────────────────────────────────
REQUEST_PARAMS = [
    # (name, type, required, default, notes)
    ("model",              "string",            True,  None,    "provider/model id or alias (openai, gpt-5.4-nano, gpt-oss)"),
    ("messages",           "array[Message]",    True,  None,    "OpenAI message array; multimodal content arrays supported"),
    ("stream",             "boolean",           False, "false", "SSE streaming, OpenAI wire format"),
    ("stream_options",     "object",            False, None,    "{include_usage: true} — usage on final chunk"),
    ("temperature",        "number",            False, "0.7",   "Per-card default_model_temperature; honored only when card lists it"),
    ("top_p",              "number",            False, "1.0",   "Nucleus sampling; per-card allowlist"),
    ("top_k",              "number",            False, None,    "Per-card allowlist"),
    ("max_tokens",         "integer",           False, "8192",  "Output token cap"),
    ("max_completion_tokens","integer",         False, None,    "Alias for max_tokens (OpenAI compat)"),
    ("tools",              "array[Tool]",       False, None,    "Function-calling tool schemas"),
    ("tool_choice",        "string | object",   False, "auto",  "auto|none|required|{type:function,function:{name:X}}"),
    ("parallel_tool_calls","boolean",           False, "true",  "Where the card lists it"),
    ("response_format",    "object",            False, None,    "json_object or json_schema (model-dependent)"),
    ("reasoning_effort",   "string",            False, None,    "minimal|low|medium|high on models that expose the levels"),
    ("seed",               "integer",           False, None,    "Reproducible results (NO random_seed aliasing — that's Mistral)"),
    ("safe",               "string | boolean",  False, None,    "POLLINATIONS-ONLY: privacy,secrets,sexual,violence,shield; true=privacy,secrets; nsfw=sexual,violence. Also accepted as Pollinations-Safe header. Defaults OFF"),
    ("cache_control",      "per-block marker",  False, None,    "Per-content-block on Gemini/Claude/Nova families; explicit, not automatic"),
]

RESPONSE_PARAMS = [
    # (name, type, notes)
    ("id",                 "string",            "e.g. chatcmpl-…"),
    ("object",             "string",            "'chat.completion'"),
    ("created",            "int (unix ts)",     "Response timestamp"),
    ("model",              "string",            "Model id used"),
    ("choices",            "array[Choice]",     "One per n"),
    ("choices[].message",  "object",            "{role:'assistant', content, tool_calls?, reasoning_content?}"),
    ("choices[].finish_reason", "string",       "stop|length|tool_calls|content_filter (OpenAI enum)"),
    ("usage",              "object | null",     "{prompt_tokens, completion_tokens, total_tokens, prompt_tokens_details.cached_tokens}. null for media models invoked through chat"),
    ("error",              "object (optional)", "Provider-side error on HTTP 200"),
]

TOOL_CALLING = {
    "format": "OpenAI-style tools[] array (function-calling convention)",
    "tool_choice": "auto|none|required|{type:function,function:{name:X}} (some upstreams like claude-fable-5.1 reject forced choices with 400)",
    "tool_call_id_format": "call_<alphanumeric> (OpenAI-style with 'call_' prefix); backend tolerates missing ids with fallback 'pollinations_tc_{i}'",
    "arguments_format": "JSON string OR parsed object (both accepted by backend)",
    "parallel_tool_calls": "bool, default true (where supported)",
    "message_shape": '{"role":"assistant","content":null,"tool_calls":[{"id":"call_abc123","type":"function","function":{"name":"get_weather","arguments":"{\\"city\\":\\"Toronto\\"}"}}]}',
    "tool_message": '{"role":"tool","content":"result","tool_call_id":"call_abc123"}',
    "gemini_caveat": "Gemini models do NOT cache when tools are present — send empty tools or set response_format if cached prefix matters",
    "doc_ref": "docs/api/POLLINATIONS_API_TECHNICAL_REFERENCE.md §Function Calling Implementation",
}

print(f"\n{CYAN}── 2. Static API surface{NC}  (from docs/api/POLLINATIONS_API_TECHNICAL_REFERENCE.md)")
print(f"  {CYAN}[Request params — POST /chat/completions body]{NC}")
print(f"    {'Name':26s} {'Type':22s} {'Req':4s} {'Default':14s} Notes")
print(f"    {'─'*26} {'─'*22} {'─'*4} {'─'*14} {'─'*40}")
for name, typ, req, default, notes in REQUEST_PARAMS:
    req_s = "yes" if req else "no"
    default_s = str(default) if default is not None else "—"
    print(f"    {name:26s} {typ:22s} {req_s:4s} {default_s:14s} {notes}")

print(f"\n  {CYAN}[Response shape — chat.completion object]{NC}")
print(f"    {'Field':28s} {'Type':20s} Notes")
print(f"    {'─'*28} {'─'*20} {'─'*40}")
for name, typ, notes in RESPONSE_PARAMS:
    print(f"    {name:28s} {typ:20s} {notes}")

print(f"\n  {CYAN}[Tool calling format]{NC}")
print(f"    format:               {TOOL_CALLING['format']}")
print(f"    tool_choice:           {TOOL_CALLING['tool_choice']}")
print(f"    tool_call_id_format:   {TOOL_CALLING['tool_call_id_format']}")
print(f"    arguments_format:      {TOOL_CALLING['arguments_format']}")
print(f"    parallel_tool_calls:   {TOOL_CALLING['parallel_tool_calls']}")
print(f"    message_shape:         {TOOL_CALLING['message_shape']}")
print(f"    tool_message:          {TOOL_CALLING['tool_message']}")
print(f"    gemini_caveat:         {TOOL_CALLING['gemini_caveat']}")
print(f"    doc_ref:               {TOOL_CALLING['doc_ref']}")

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

# Category breakdown — Pollinations uses 'category' to filter chat vs media
cats = Counter(m.get("category", "?") for m in models)
print(f"  By category:            {dict(cats)}")

# ───────────────────────────────────────────────────────────────────────────
# Section 4 — Card field availability (which fields each live model exposes)
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 4. Card field availability (live /v1/models) ──{NC}")
# Standard OpenAI fields:
STANDARD_FIELDS = ["id", "object", "created", "owned_by"]
# Rich Pollinations-specific fields (health is POLLINATIONS-EXCLUSIVE):
RICH_FIELDS = [
    "aliases", "category", "community", "title", "description",
    "input_modalities", "output_modalities", "supported_endpoints",
    "pricing", "capabilities", "supported_parameters", "tools",
    "reasoning", "context_length", "health",
    # paid_only is ○ missing here — lives ONLY on bare /models
    "paid_only",
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

# Sub-block — bare /models feed (different schema, name-keyed, paid_only lives here)
print(f"\n  {CYAN}[Bare /models feed — name-keyed, exclusive fields]{NC}")
if bare:
    BARE_FIELDS = [
        "name", "paid_only", "pricing_default_label", "pricing_variants",
        "publisher", "is_specialized", "category", "community",
        "pricing", "health", "context_length", "tools",
    ]
    print(f"  (fetched {len(bare)} cards — bare schema is name-keyed, not id-keyed)")
    print(f"  {'Field':30s} {'Available':12s} {'Count':12s}")
    print(f"  {'─'*30} {'─'*12} {'─'*12}")
    for field in BARE_FIELDS:
        n = sum(1 for m in bare if field in m)
        if n == 0:
            status = f"{YELLOW}○ missing{NC}"
        elif n == len(bare):
            status = f"{GREEN}✓ all{NC}"
        else:
            status = f"{YELLOW}~ partial{NC}"
        print(f"  {field:30s} {n}/{len(bare):<10d}  {status}")
    # paid_only is the entire point of this feed — break it out explicitly
    po_t = sum(1 for m in bare if m.get("paid_only") is True)
    po_f = sum(1 for m in bare if m.get("paid_only") is False)
    po_m = sum(1 for m in bare if "paid_only" not in m)
    print(f"\n  paid_only breakdown:    True={po_t}  False={po_f}  missing={po_m}")
    print(f"  free TIER (not paid_only, any category): {po_f + po_m}  ← Quest-Pollen-eligible")
else:
    print(f"  {YELLOW}✗ bare /models not fetched — paid_only unavailable this run{NC}")

# Availability summary for per-model detail
SIZE_AVAILABLE = any(m.get("size") for m in models)
PRICING_AVAILABLE = any("pricing" in m for m in models)
CTX_AVAILABLE = any(m.get("context_length") for m in models)
HEALTH_AVAILABLE = any("health" in m for m in models)
print(f"\n  Availability summary (for per-model detail):")
print(f"    Pricing available:           {'yes' if PRICING_AVAILABLE else 'no'}  (live per-token pollen → ×1M for per-1M)")
print(f"    Context length available:    {'yes' if CTX_AVAILABLE else 'no'}  (live context_length)")
print(f"    Model size available:        {'yes' if SIZE_AVAILABLE else 'no'}  (always '—' for Pollinations — not exposed)")
print(f"    Health telemetry available:  {'yes' if HEALTH_AVAILABLE else 'no'}  (POLLINATIONS-EXCLUSIVE: status/success_rate/requests)")

# ───────────────────────────────────────────────────────────────────────────
# Section 5 — Per-model detail
# Free-tier rule mirrors _card_is_free() in agentkthx/plugins/pollinations/pollinations.py.
# Pollinations has THREE free concepts (documented in Section 6); here we use
# the zero-cost rule (currency-only pricing dict = TRUE zero-cost community variant).
# Catalog is the source of truth for max_tok (always 8192). Pricing comes from
# the live feed (per-token pollen × 1M = per-1M pollen).
# ───────────────────────────────────────────────────────────────────────────
CATALOG = {
    "openai/gpt-5.4-nano":      {"ctx": 400000, "max_tok": 8192, "in": 0.15, "out": 0.9375, "family": "openai"},
    "openai/gpt-5.4-mini":      {"ctx": 128000, "max_tok": 8192, "in": 0.75, "out": 3.0, "family": "openai"},
    "openai/gpt-5.4":          {"ctx": 128000, "max_tok": 8192, "in": 1.875, "out": 7.5, "family": "openai"},
    "openai/gpt-oss-20b":       {"ctx": 128000, "max_tok": 8192, "in": 0.05, "out": 0.3, "family": "openai"},
    "z-ai/glm-5.3-flash":      {"ctx": 128000, "max_tok": 8192, "in": 0.1, "out": 0.4, "family": "z-ai"},
    "z-ai/glm-5.3-flashx":    {"ctx": 1000000, "max_tok": 8192, "in": 0.15, "out": 0.6, "family": "z-ai"},
    "deepseek/deepseek-v4-flash":{"ctx": 128000, "max_tok": 8192, "in": 0.1, "out": 0.4, "family": "deepseek"},
    "anthropic/claude-sonnet-4.6":{"ctx": 200000, "max_tok": 8192, "in": 1.875, "out": 9.375, "family": "anthropic"},
    "anthropic/claude-haiku-4.5":{"ctx": 200000, "max_tok": 8192, "in": 0.625, "out": 3.125, "family": "anthropic"},
    "google/gemini-3.7-flash": {"ctx": 1000000, "max_tok": 8192, "in": 0.1, "out": 0.4, "family": "google"},
    "mistralai/mistral-small-4":{"ctx": 262144, "max_tok": 8192, "in": 0.2, "out": 0.5, "family": "mistralai"},
    "meta/llama-4-scout":      {"ctx": 128000, "max_tok": 8192, "in": 0.075, "out": 0.3, "family": "meta"},
    "qwen/qwen3.8-flash":      {"ctx": 128000, "max_tok": 8192, "in": 0.1, "out": 0.4, "family": "qwen"},
}

def catalog_key(live_id: str) -> str:
    """Pollinations ids are canonical provider/model form. No normalization needed."""
    return live_id

def catalog_lookup(live_id: str):
    """Direct identity lookup. Returns (catalog_key_or_None, catalog_meta_or_None)."""
    if live_id in CATALOG:
        return live_id, CATALOG[live_id]
    return None, None

def _to_float(s):
    try: return float(s)
    except (TypeError, ValueError): return None

# Mirror of _card_is_free() in pollinations.py.
# Works on both live card shape (pricing.promptTextTokens/completionTextTokens
# per-token strings) and static catalog shape (pricing.input/output per-1M floats).
# The gateway encodes TRUE zero-cost models as a CURRENCY-ONLY pricing dict
# ({"currency": "pollen"} with NO price fields at all) — the ':free'/'-free'
# community variants. Zero-valued price fields never appear on the live feed.
def is_free(card_or_meta: dict) -> bool:
    pricing = (card_or_meta or {}).get("pricing") or {}
    # Currency-only pricing (price fields absent) = zero-cost tier
    if pricing and set(pricing.keys()) <= {"currency"}:
        return True
    if "promptTextTokens" in pricing or "completionTextTokens" in pricing:
        prompt = _to_float(pricing.get("promptTextTokens"))
        completion = _to_float(pricing.get("completionTextTokens"))
        return bool(prompt is not None and completion is not None and prompt == 0.0 and completion == 0.0)
    # Static catalog shape (pricing.input/output per-1M floats)
    return pricing.get("input", -1) == 0.0 and pricing.get("output", -1) == 0.0

def catalog_entry_is_free(meta: dict) -> bool:
    """Direct check on catalog meta's top-level in/out (catalog has no 'pricing' key)."""
    return meta.get("in", -1) == 0.0 and meta.get("out", -1) == 0.0

def family_of(model_id: str, m_card: dict) -> str:
    """Family = provider prefix in provider/model form."""
    if "/" in model_id:
        return model_id.split("/", 1)[0]
    # Fallback for community variants without provider prefix
    return m_card.get("category", "other") or "other"

def get_ctx(m_card: dict) -> str:
    """Live context_length is source of truth; catalog is fallback."""
    live = m_card.get("context_length")
    if live:
        return str(live)
    _, meta = catalog_lookup(m_card.get("id", ""))
    if meta and meta.get("ctx"):
        return f"{meta['ctx']}(cat)"
    return "—"

def get_max_tok(model_id: str) -> str:
    """Catalog is source of truth (always 8192)."""
    _, meta = catalog_lookup(model_id)
    if meta and meta.get("max_tok"):
        return f"{meta['max_tok']}(cat)"
    return "—"

def get_size(m_card: dict) -> str:
    # Pollinations cards do not expose model size — always '—'
    return "—"

def get_pricing(m_card: dict) -> str:
    """Live per-token pollen × 1M = per-1M pollen. Format: {in}p/{out}p(live).
    For currency-only pricing (free community variant), show '0p/0p(free)'."""
    pricing = m_card.get("pricing") or {}
    if not pricing:
        return "—"
    # Currency-only = zero-cost community variant
    if set(pricing.keys()) <= {"currency"}:
        return "0p/0p(free)"
    p_in = _to_float(pricing.get("promptTextTokens"))
    p_out = _to_float(pricing.get("completionTextTokens"))
    if p_in is None or p_out is None:
        return "?p/?p(live)"
    # Per-token × 1M = per-1M
    per_m_in = p_in * 1_000_000
    per_m_out = p_out * 1_000_000
    return f"{per_m_in:g}p/{per_m_out:g}p(live)"

# Non-chat filter: Pollinations uses a 'category' field, not substring patterns.
# card.get("category") not in (None, "text") filters out image/video/audio/
# embedding/3d cards. Use this in Section 5/3.
def is_chat(card: dict) -> bool:
    return card.get("category") in (None, "text")

chat_models = [m for m in models if is_chat(m)]

print(f"\n{CYAN}── 5. Per-model detail ──{NC}")
print(f"  (Showing {len(chat_models)} chat/text models of {len(models)} total — non-chat filtered out)")
print(f"  (Free rule: currency-only pricing dict = TRUE zero-cost community variant)")
print(f"  (Pricing: live per-token pollen × 1M = per-1M pollen; 'p' suffix = pollen)")
print(f"")
print(f"  {'Model':42s} {'Family':14s} {'Ctx':12s} {'MaxTok':12s} {'Size':6s} {'Pricing':22s} {'Free':5s} {'Paid':5s}")
print(f"  {'─'*42} {'─'*14} {'─'*12} {'─'*12} {'─'*6} {'─'*22} {'─'*5} {'─'*5}")

# Sort: catalog entries first (alphabetical), then unmatched live ids alphabetically
def sort_key(m):
    mid = m.get("id", "")
    _, meta = catalog_lookup(mid)
    return (0 if meta else 1, mid)

for m in sorted(chat_models, key=sort_key):
    mid = m.get("id", "?")
    fam = family_of(mid, m)
    ctx = get_ctx(m)
    max_tok = get_max_tok(mid)
    size = get_size(m)
    pricing = get_pricing(m)
    free = is_free(m)
    paid = not free
    free_s = f"{GREEN}yes{NC}" if free else "no"
    paid_s = "yes" if paid else f"{GREEN}—{NC}"
    print(f"  {mid:42s} {fam:14s} {ctx:12s} {max_tok:12s} {size:6s} {pricing:22s} {free_s:5s} {paid_s:5s}")

# ───────────────────────────────────────────────────────────────────────────
# Section 6 — Free vs full catalog
# Pollinations has THREE free concepts (unique to this backend):
#   1. Zero-cost community: ':free'/'-free' variants — detected via currency-only
#      pricing dict on /v1/models feed (TRUE zero pollen per token)
#   2. Free TIER (Quest-Pollen-eligible): models the key is entitled to run —
#      detected via `paid_only != True` on bare /models feed (metered pollen,
#      but runnable on the free grant)
#   3. Legacy anonymous: no key, IP-rate-limited — legacy text.pollinations.ai
#      surface (not part of this probe)
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 6. Free vs full catalog ──{NC}")

# Tier 1: zero-cost community (currency-only pricing on /v1/models feed)
zero_cost = [m for m in models if is_free(m)]
paid_models = [m for m in models if not is_free(m)]

print(f"  Live total (/v1/models):              {len(models)}")
print(f"  Chat/text only:                        {len(chat_models)}")
print(f"  Zero-cost community (is_free=True):    {len(zero_cost)}")
if zero_cost:
    for m in zero_cost[:20]:
        h = m.get("health") or {}
        sr = h.get("success_rate")
        sr_s = f"{sr:g}" if sr is not None else "?"
        print(f"    {GREEN}✓{NC} {m.get('id','?'):<40s} sr={sr_s}")
    if len(zero_cost) > 20:
        print(f"    ... + {len(zero_cost) - 20} more")
else:
    print(f"    (none — no ':free'/'-free' community variants surfaced this run)")
print(f"  Paid (is_free=False):                  {len(paid_models)}")
if paid_models:
    for m in paid_models[:5]:
        print(f"    · {m.get('id','?')}")
    if len(paid_models) > 5:
        print(f"    ... + {len(paid_models) - 5} more")

# Catalog drift (Pollinations ids are canonical — identity match, no normalization)
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

print(f"\n  Static POLLINATIONS_MODELS catalog size:  {len(CATALOG)}")
print(f"  In live API but NOT in catalog:           {len(in_live_not_catalog)}")
print(f"    (EXPECTED — live API has 300+ models; catalog pins only 13 reference ids)")
for m in in_live_not_catalog[:10]:
    print(f"    + {m}")
if len(in_live_not_catalog) > 10:
    print(f"    ... + {len(in_live_not_catalog) - 10} more")
print(f"  In catalog but NOT in live API:           {len(in_catalog_not_live)}")
for m in in_catalog_not_live:
    print(f"    - {m}")

# Catalog free subset (catalog entries where in==0.0 AND out==0.0)
catalog_free = sorted(k for k in CATALOG if catalog_entry_is_free(CATALOG[k]))
catalog_paid = sorted(k for k in CATALOG if not catalog_entry_is_free(CATALOG[k]))
print(f"\n  Catalog free subset (zero-cost):          {len(catalog_free)}")
if catalog_free:
    for m in catalog_free:
        print(f"    {GREEN}✓{NC} {m}")
else:
    print(f"    (none — Pollinations catalog pins no ':free' variants; they live only on the live feed)")
print(f"  Catalog paid subset:                     {len(catalog_paid)}")

# Tier 2 — free TIER from bare /models feed (paid_only != True)
print(f"\n  {CYAN}[Tier 2 — free TIER from bare /models feed]{NC}")
if bare:
    po_t = sum(1 for m in bare if m.get("paid_only") is True)
    po_f = sum(1 for m in bare if m.get("paid_only") is False)
    po_m = sum(1 for m in bare if "paid_only" not in m)
    print(f"  Bare /models total:                       {len(bare)}")
    print(f"  paid_only: True={po_t}  False={po_f}  missing={po_m}")
    print(f"  Free TIER (paid_only != True):            {po_f + po_m}  ← Quest-Pollen-eligible")
    ft = [m for m in bare
          if m.get("paid_only") is not True and m.get("category") == "text"]
    print(f"  Free TIER text models:                    {len(ft)}")
    comm_ft = [m for m in ft if m.get("community")]
    print(f"  Free TIER text + community:               {len(comm_ft)}  ← the website's source:community free view")

    # TRUE zero-cost text models from bare feed (currency-only pricing)
    zero_bare = [m for m in ft
                 if set((m.get("pricing") or {}).keys()) <= {"currency"}]

    def _sr(m):
        v = (m.get("health") or {}).get("success_rate")
        return v if v is not None else -1

    zero_bare.sort(key=lambda m: (-_sr(m), m.get("name", "")))
    print(f"\n  TRUE zero-cost text models ({len(zero_bare)}), health-ordered:")
    for m in zero_bare[:10]:
        h = m.get("health") or {}
        s = h.get("success_rate")
        s = f"{s:g}" if s is not None else "?"
        print(f"    {m.get('name','?'):<56s} sr={s:<8s} "
              f"ctx={str(m.get('context_length')):<8s} "
              f"tools={str(m.get('tools'))}")
    if len(zero_bare) > 10:
        print(f"    ... + {len(zero_bare) - 10} more")

    # Healthiest free-TIER community text models (top 10)
    top_comm = sorted(comm_ft, key=lambda m: (-_sr(m), m.get("name", "")))[:10]
    print(f"\n  healthiest free-TIER community text models (top 10 of {len(comm_ft)}):")
    for m in top_comm:
        h = m.get("health") or {}
        s = h.get("success_rate")
        s = f"{s:g}" if s is not None else "?"
        print(f"    {m.get('name','?'):<56s} sr={s:<8s} "
              f"ctx={str(m.get('context_length')):<8s} "
              f"tools={str(m.get('tools'))}")
else:
    print(f"  {YELLOW}✗ bare /models unavailable — paid_only discovery skipped this run{NC}")

# healthy_fallbacks() top-3 simulation
# (filters: category==text, NOT community, has tools; sorts by -health.success_rate then price asc)
print(f"\n  {CYAN}[healthy_fallbacks() top-3 simulation]{NC}")
print(f"  Filters: category=='text', NOT community, has tools")
print(f"  Sort:    -health.success_rate, then price asc")
text_pool = [m for m in models
             if m.get("category") == "text"
             and not m.get("community")
             and m.get("tools")]

def _price(m):
    try: return float(m.get("pricing", {}).get("promptTextTokens", 1) or 1)
    except (TypeError, ValueError): return 1.0

def _health_sr(m):
    v = (m.get("health") or {}).get("success_rate")
    return v if v is not None else -1

text_pool.sort(key=lambda m: (-_health_sr(m), _price(m)))
print(f"  Candidate pool: {len(text_pool)} models")
for m in text_pool[:3]:
    h = m.get("health") or {}
    sr = h.get("success_rate")
    sr_s = f"{sr:g}" if sr is not None else "?"
    print(f"    {m.get('id','?'):<40s} success_rate={sr_s:<8s} "
          f"promptToken={_price(m)}")

# Deprecation field — Pollinations cards do NOT carry it (unlike Mistral)
deprecated = [m for m in models if m.get("deprecation")]
print(f"\n  Deprecation field:                        {len(deprecated)} live models carry it")
if deprecated:
    print(f"  {YELLOW}⚠ Pollinations started surfacing deprecation fields — investigate{NC}")
else:
    print(f"  (Pollinations cards do NOT carry 'deprecation' — unlike Mistral)")

# Keyed vs anonymous feed scoping note
if os.environ.get("POLLINATIONS_API_KEY", "").strip():
    print(f"\n  {CYAN}[Keyed feed note]{NC}")
    print(f"  POLLINATIONS_API_KEY was set — the {len(models)} cards above are the")
    print(f"  KEY-scoped entitlement subset (~134 cards). Compare to bare feed")
    print(f"  free-TIER count above for parity check (the keyed feed IS the")
    print(f"  entitlement = free-tier subset).")
else:
    print(f"\n  {CYAN}[Anonymous feed note]{NC}")
    print(f"  POLLINATIONS_API_KEY not set — the {len(models)} cards above are the")
    print(f"  ANONYMOUS catalog (~307 cards). Set the key to scope to entitlements (~134).")

print(f"\n{CYAN}{'─'*78}{NC}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_pollinations.json        (/v1/models)")
print(f"                  /tmp/agentkthx_probe_pollinations_bare.json   (bare /models)")
print(f"{CYAN}{'─'*78}{NC}")
PYEOF
