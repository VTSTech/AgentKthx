#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_zai.sh — Validate ZAI API Technical Reference (expanded)
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls, no tokens burned).
# Requires: ZAI_API_KEY in env  (https://z.ai)
# Usage:    bash probe_zai.sh
# Output:   /tmp/agentkthx_probe_zai.json  (raw API response)
#
# Note:     ZAI's /models endpoint is undocumented and the path is ambiguous.
#           This probe tries BOTH /api/paas/v4/models AND /paas/v4/models,
#           and falls back gracefully to catalog-only data if both 404.
#           A separate auth-verify call to the base URL confirms the key is
#           valid even if /models is unreachable.
#
# Report sections:
#   1. Endpoint           — base URL, auth shape, HTTP status, dual-path
#                            probe, auth verify, raw JSON path
#   2. Static API surface  — request params, response shape, tool calling
#                            format (sourced from docs/api/ZAI_API_
#                            TECHNICAL_REFERENCE.md; hardcoded — slow-moving)
#   3. Live /models        — top-level keys, total count, sample card
#                            (likely empty — endpoint undocumented)
#   4. Card field avail.   — which fields each live model exposes
#                            (rich fields labeled ○ missing — catalog is
#                            source of truth; no non-chat filter — catalog
#                            is chat-only GLM models)
#   5. Per-model detail     — id, family, ctx, max_tok, size (always "—"),
#                            pricing ($X/$Y per 1M from catalog), is_free,
#                            is_paid  (iterates catalog — 17 GLM models)
#   6. Free vs full catalog — free subset (2 models; note glm-5.3-flash
#                            false-positive trap), paid subset, static
#                            catalog drift (live typically returns subset)
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

API_KEY="${ZAI_API_KEY:-}"
[ -z "$API_KEY" ] && echo "ERROR: Set ZAI_API_KEY first: export ZAI_API_KEY=..." >&2 && exit 1

BASE_URL="${ZAI_BASE_URL:-https://api.z.ai}"

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Endpoint (dual-path probe + auth verify)
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}${BOLD}═══ ZAI API Technical Reference Probe ═══${NC}"
echo ""
echo -e "${CYAN}── 1. Endpoint ──${NC}"
echo "  Base URL:     ${BASE_URL}"
echo "  Chat path:    POST /api/paas/v4/chat/completions"
echo "  Models path:  GET /api/paas/v4/models  (primary)"
echo "                GET /paas/v4/models      (fallback — path ambiguous)"
echo "  Auth:         Bearer \$ZAI_API_KEY (len=${#API_KEY})"
echo ""

# ZAI's /models endpoint is undocumented. Try BOTH paths.
MODELS_FOUND=0
for path in "/api/paas/v4/models" "/paas/v4/models"; do
    URL="${BASE_URL}${path}"
    echo "  Trying:       GET ${URL}"
    HTTP_CODE=$(curl -s -m 15 -o /tmp/agentkthx_probe_zai.json -w "%{http_code}" \
        -H "Authorization: Bearer $API_KEY" \
        -H "User-Agent: AgentKthx-probe/0.x" \
        -H "Accept: application/json" \
        "$URL" 2>/dev/null || echo "000")

    if [ "$HTTP_CODE" = "200" ]; then
        echo -e "  ${GREEN}✓ HTTP 200 — endpoint exists${NC}  (raw JSON: /tmp/agentkthx_probe_zai.json)"
        if python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_zai.json')); assert isinstance(d.get('data'), list) or isinstance(d.get('models'), list) or isinstance(d, list)" 2>/dev/null; then
            MODELS_FOUND=1
            break
        else
            echo -e "  ${YELLOW}⚠ HTTP 200 but not a JSON model list. Body (first 300 bytes):${NC}"
            head -c 300 /tmp/agentkthx_probe_zai.json
            echo
        fi
    elif [ "$HTTP_CODE" = "404" ]; then
        echo -e "  ${YELLOW}○ HTTP 404 — path not found, trying fallback${NC}"
    elif [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "403" ]; then
        echo -e "  ${RED}✗ HTTP $HTTP_CODE — auth failed${NC}"
    else
        echo -e "  ${YELLOW}○ HTTP $HTTP_CODE${NC}"
    fi
done

if [ "$MODELS_FOUND" = "0" ]; then
    echo -e "  ${YELLOW}⚠ Neither /models path returned a valid model list.${NC}"
    echo "  Continuing with empty live set — catalog (Section 5) is source of truth."
    echo '{"data":[]}' > /tmp/agentkthx_probe_zai.json
fi

# Auth verify with base URL (sanity check — key valid even if /models 404s)
echo ""
echo "  Auth verify:  GET ${BASE_URL}  (sanity check, not a models endpoint)"
AUTH_CODE=$(curl -s -m 5 -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $API_KEY" \
    "${BASE_URL}" 2>/dev/null || echo "000")
echo "                HTTP ${AUTH_CODE}  (200/401/403/404 all confirm key reaches the API)"

# ═══════════════════════════════════════════════════════════════════════════
# Sections 2–6 — Python analysis (single heredoc, parses JSON + emits report)
# ═══════════════════════════════════════════════════════════════════════════
python3 << 'PYEOF'
import json

CYAN = '\033[0;36m'; GREEN = '\033[0;32m'; YELLOW = '\033[0;33m'; RED = '\033[0;31m'; NC = '\033[0m'

d = json.load(open("/tmp/agentkthx_probe_zai.json"))
# ZAI /models shape is undocumented; accept {data:[...]}, {models:[...]}, or top-level list
if isinstance(d, list):
    models = d
elif isinstance(d.get("data"), list):
    models = d["data"]
elif isinstance(d.get("models"), list):
    models = d["models"]
else:
    models = []

# ───────────────────────────────────────────────────────────────────────────
# Section 2 — Static API surface (sourced from docs/api/ZAI_API_TECHNICAL_REFERENCE.md)
# Hardcoded in the probe because the doc is slow-moving and parsing it inline
# would be brittle. Cite the doc so the source of truth is visible.
# ───────────────────────────────────────────────────────────────────────────
REQUEST_PARAMS = [
    # (name, type, required, default, notes)
    ("model",              "string",            True,  None,    "Model id from catalog or accepted live id"),
    ("messages",           "array[Message]",    True,  None,    "Roles: system/user/assistant/tool; content string or multimodal array"),
    ("temperature",        "float",             False, "0.7",   "Sampling temperature"),
    ("top_p",              "float",             False, "0.95",  "Nucleus sampling"),
    ("max_tokens",         "int",               False, "131072","Output cap (NOT input+output)"),
    ("stream",             "bool",              False, "false", "SSE stream; emits data:{...} + data:[DONE]"),
    ("do_sample",          "bool",              False, "true",  "ZAI-SPECIFIC — not in OpenAI spec"),
    ("thinking",           "object",            False, None,    "ZAI-SPECIFIC: {type:enabled, clear_thinking:true} — GLM-4.5+ reasoning"),
    ("reasoning_effort",   "string",            False, None,    "max|high|low — GLM-5.x only ('max' is ZAI-only)"),
    ("tools",              "array[Tool]",       False, None,    "OpenAI-style; type can be function/web_search/retrieval"),
    ("tool_choice",        "string | object",   False, "auto",  "auto|none|required|{type:function,function:{name:X}}"),
    ("tool_stream",        "bool",              False, "false", "ZAI-SPECIFIC — stream tool-call deltas separately"),
    ("response_format",    "object",            False, None,    "{type: text|json_object} (no json_schema documented)"),
    ("stop",               "string | array",    False, None,    "Stop sequences"),
    ("request_id",         "string",            False, None,    "ZAI-SPECIFIC — opaque correlation id, echoed in response"),
    ("user_id",            "string",            False, None,    "ZAI-SPECIFIC — end-user correlation"),
    ("presence_penalty",   "float",             False, "0.0",   ""),
    ("frequency_penalty",  "float",             False, "0.0",   ""),
]

RESPONSE_PARAMS = [
    # (name, type, notes)
    ("id",                              "string",        "e.g. chatcmpl-123456"),
    ("request_id",                      "string",        "ZAI-SPECIFIC — echoed from request"),
    ("created",                         "int (unix ts)", "Response timestamp"),
    ("model",                           "string",        "Model id used"),
    ("choices",                         "array[Choice]", "One per n; backend reads choices[0]"),
    ("choices[].message",               "object",        "{role:'assistant', content, reasoning_content?, tool_calls?}"),
    ("choices[].message.reasoning_content","string",      "ZAI-SPECIFIC — chain-of-thought for GLM-4.5+ thinking models"),
    ("choices[].finish_reason",         "string",        "stop|tool_calls|length|sensitive(ZAI)|model_context_window_exceeded(ZAI)|network_error(ZAI)"),
    ("usage",                           "object",        "{prompt_tokens, completion_tokens, total_tokens, prompt_tokens_details.cached_tokens(ZAI)}"),
    ("web_search",                      "array",         "ZAI-SPECIFIC — populated when tools includes web_search; {title, content, link, media, icon, refer, publish_date}"),
]

TOOL_CALLING = {
    "format": "OpenAI-style tools[] array (function-calling convention)",
    "tool_types": "function (user-defined) | web_search (ZAI-hosted retrieval) | retrieval (ZAI-hosted RAG)",
    "tool_choice": "auto|none|required|{type:function,function:{name:X}}",
    "tool_call_id_format": "call_<numeric> (OpenAI-style with 'call_' prefix, e.g. 'call_123')",
    "arguments_format": "JSON string OR parsed object (both accepted by backend)",
    "message_shape": '{"role":"assistant","content":"...","tool_calls":[{"id":"call_123","type":"function","function":{"name":"get_weather","arguments":"{\\"city\\":\\"Beijing\\"}"}}]}',
    "tool_message": '{"role":"tool","content":"<result>","tool_call_id":"call_123"}',
    "react_fallback": "400 'does not support tools' -> backend retries with tools+tool_choice stripped (ReAct fallback)",
    "doc_ref": "docs/api/ZAI_API_TECHNICAL_REFERENCE.md §Function Calling Implementation",
}

print(f"\n{CYAN}── 2. Static API surface{NC}  (from docs/api/ZAI_API_TECHNICAL_REFERENCE.md)")
print(f"  {CYAN}[Request params — POST /api/paas/v4/chat/completions body]{NC}")
print(f"    {'Name':24s} {'Type':22s} {'Req':4s} {'Default':14s} Notes")
print(f"    {'─'*24} {'─'*22} {'─'*4} {'─'*14} {'─'*40}")
for name, typ, req, default, notes in REQUEST_PARAMS:
    req_s = "yes" if req else "no"
    default_s = str(default) if default is not None else "—"
    print(f"    {name:24s} {typ:22s} {req_s:4s} {default_s:14s} {notes}")

print(f"\n  {CYAN}[Response shape — chat.completion object]{NC}")
print(f"    {'Field':38s} {'Type':18s} Notes")
print(f"    {'─'*38} {'─'*18} {'─'*40}")
for name, typ, notes in RESPONSE_PARAMS:
    print(f"    {name:38s} {typ:18s} {notes}")

print(f"\n  {CYAN}[Tool calling format]{NC}")
print(f"    format:              {TOOL_CALLING['format']}")
print(f"    tool_types:          {TOOL_CALLING['tool_types']}")
print(f"    tool_choice:         {TOOL_CALLING['tool_choice']}")
print(f"    tool_call_id_format: {TOOL_CALLING['tool_call_id_format']}")
print(f"    arguments_format:    {TOOL_CALLING['arguments_format']}")
print(f"    message_shape:       {TOOL_CALLING['message_shape']}")
print(f"    tool_message:        {TOOL_CALLING['tool_message']}")
print(f"    react_fallback:      {TOOL_CALLING['react_fallback']}")
print(f"    doc_ref:             {TOOL_CALLING['doc_ref']}")

# ───────────────────────────────────────────────────────────────────────────
# Section 3 — Live /models response (likely empty — endpoint undocumented)
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 3. Live /models response ──{NC}")
print(f"  Total models:           {len(models)}")
if isinstance(d, dict):
    print(f"  Top-level keys:         {list(d.keys())}")
elif isinstance(d, list):
    print(f"  Top-level type:         list (raw)")
if models:
    sample = models[0]
    if isinstance(sample, dict):
        print(f"  Sample model object:    {sample.get('id', '?')}")
        print(f"  Sample card keys:       {list(sample.keys())}")
    else:
        print(f"  Sample entry:            {sample!r}")
else:
    print(f"  (empty — ZAI /models endpoint undocumented; catalog is source of truth)")

# ───────────────────────────────────────────────────────────────────────────
# Section 4 — Card field availability (live models)
# ZAI /models endpoint is undocumented. Expected card shape is minimal {id}
# (possibly with object/created/owned_by). Backend enriches everything else
# from the static catalog. Rich fields are labeled ○ missing.
#
# Non-chat filter: NONE — ZAI catalog contains only chat-capable GLM models.
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 4. Card field availability (live models) ──{NC}")
print(f"  Note: ZAI /models endpoint is undocumented. Rich fields expected ○ missing")
print(f"        (catalog is source of truth). Non-chat filter: NONE — catalog is")
print(f"        chat-only GLM models.")
STANDARD_FIELDS = ["id", "object", "created", "owned_by"]
RICH_FIELDS = [
    "capabilities", "max_context_length", "default_model_temperature",
    "aliases", "deprecation", "description", "name",
    "pricing", "size", "max_tokens", "max_output_tokens",
]
all_fields = STANDARD_FIELDS + RICH_FIELDS
total = len(models)
print(f"\n  {'Field':30s} {'Available':12s} {'Count':12s}")
print(f"  {'─'*30} {'─'*12} {'─'*12}")
for field in all_fields:
    if total == 0:
        n = 0
        status = f"{YELLOW}○ no live models{NC}"
    else:
        n = sum(1 for m in models if isinstance(m, dict) and field in m)
        if n == 0:
            status = f"{YELLOW}○ missing{NC}"
        elif n == total:
            status = f"{GREEN}✓ all{NC}"
        else:
            status = f"{YELLOW}~ partial{NC}"
    print(f"  {field:30s} {n}/{total:<10d}  {status}")

SIZE_AVAILABLE = any(isinstance(m, dict) and m.get("size") for m in models)
PRICING_AVAILABLE = any(isinstance(m, dict) and "pricing" in m for m in models)
MAXCTX_AVAILABLE = any(isinstance(m, dict) and (m.get("max_context_length") or m.get("context_length")) for m in models)
MAXTOK_AVAILABLE = any(isinstance(m, dict) and (m.get("max_tokens") or m.get("max_output_tokens")) for m in models)
print(f"\n  Availability summary (for per-model detail):")
print(f"    Model size available:        {'yes' if SIZE_AVAILABLE else 'no'}  (ZAI cards typically omit; column shows '—')")
print(f"    Pricing available:           {'yes' if PRICING_AVAILABLE else 'no'}  (catalog is source of truth)")
print(f"    Max context available:       {'yes' if MAXCTX_AVAILABLE else 'no'}  (catalog is source of truth)")
print(f"    Max tokens available:        {'yes' if MAXTOK_AVAILABLE else 'no'}  (catalog is source of truth)")

# ───────────────────────────────────────────────────────────────────────────
# Section 5 — Per-model detail
# Catalog is the source of truth (17 GLM models). Live API may return empty
# or a subset; we iterate the CATALOG so all known models are visible.
#
# Free-tier rule mirrors _is_free_model() in agentkthx/plugins/zai/zai.py:
#   Free iff catalog pricing.input == 0.0 AND pricing.output == 0.0.
#   No ':free' suffix convention. No whitelist. Pricing-derived only.
#   glm-5.3-flash is the documented false-positive trap:
#     name contains "flash" but pricing is $0.15/$0.50, so NOT free.
# ───────────────────────────────────────────────────────────────────────────
CATALOG = {
    "glm-5.1":              {"ctx": 204800, "max_tok": 131072, "in": 1.40, "out": 4.40, "family": "glm-5"},
    "glm-5.2":              {"ctx": 1048576, "max_tok": 131072, "in": 1.40, "out": 4.40, "family": "glm-5"},
    "glm-5":                {"ctx": 204800, "max_tok": 131072, "in": 1.00, "out": 3.20, "family": "glm-5"},
    "glm-5-turbo":          {"ctx": 204800, "max_tok": 131072, "in": 1.20, "out": 4.00, "family": "glm-5"},
    "glm-5.3":              {"ctx": 1048576, "max_tok": 131072, "in": 1.40, "out": 4.40, "family": "glm-5"},
    "glm-5.3-flash":        {"ctx": 1048576, "max_tok": 131072, "in": 0.15, "out": 0.50, "family": "glm-5"},  # NOT free despite name
    "glm-5.3-flashx":       {"ctx": 1048576, "max_tok": 131072, "in": 0.37, "out": 1.25, "family": "glm-5"},
    "glm-4.7":              {"ctx": 204800, "max_tok": 131072, "in": 0.60, "out": 2.20, "family": "glm-4"},
    "glm-4.7-flash":        {"ctx": 204800, "max_tok": 131072, "in": 0.00, "out": 0.00, "family": "glm-4"},  # FREE
    "glm-4.7-flashx":       {"ctx": 204800, "max_tok": 131072, "in": 0.07, "out": 0.40, "family": "glm-4"},
    "glm-4.6":              {"ctx": 204800, "max_tok": 131072, "in": 0.60, "out": 2.20, "family": "glm-4"},
    "glm-4.5":              {"ctx": 132000, "max_tok": 98304, "in": 0.60, "out": 2.20, "family": "glm-4"},
    "glm-4.5-flash":        {"ctx": 132000, "max_tok": 98304, "in": 0.00, "out": 0.00, "family": "glm-4"},  # FREE
    "glm-4.5-air":          {"ctx": 132000, "max_tok": 98304, "in": 0.20, "out": 1.10, "family": "glm-4"},
    "glm-4.5-x":            {"ctx": 132000, "max_tok": 98304, "in": 2.20, "out": 8.90, "family": "glm-4"},
    "glm-4.5-airx":         {"ctx": 132000, "max_tok": 98304, "in": 1.10, "out": 4.50, "family": "glm-4"},
    "glm-4-32b-0414-128k":  {"ctx": 131072, "max_tok": 16384, "in": 0.10, "out": 0.10, "family": "glm-4"},
}

def catalog_key(live_id):
    """Strip provider prefix (e.g. 'zai/glm-4.5-flash' -> 'glm-4.5-flash')."""
    return live_id.split("/")[-1] if "/" in live_id else live_id

def catalog_lookup(live_id):
    k = catalog_key(live_id)
    if k in CATALOG:
        return k, CATALOG[k]
    return None, None

# Mirror of _is_free_model() in zai.py:
# Free iff catalog pricing.input == 0.0 AND pricing.output == 0.0.
# No ':free' suffix convention. No whitelist. Pricing-derived only.
# glm-5.3-flash is the documented false-positive trap:
#   name contains "flash" but pricing is $0.15/$0.50, so NOT free.
def is_free(model_id):
    key = model_id.split("/")[-1] if "/" in model_id else model_id
    meta = CATALOG.get(key)
    if not meta:
        return False
    return meta["in"] == 0.0 and meta["out"] == 0.0

def get_pricing(model_id):
    meta = CATALOG.get(model_id)
    if not meta:
        return "—"
    if meta["in"] == 0.0 and meta["out"] == 0.0:
        return "$0/$0(cat)"
    return f"${meta['in']}/${meta['out']}(cat)"

print(f"\n{CYAN}── 5. Per-model detail ──{NC}")
print(f"  (Free rule: catalog pricing.input==0 AND pricing.output==0)")
print(f"  Catalog is the source of truth — 17 GLM models. Live API may return subset.")
print(f"  Size column always '—' (ZAI doesn't expose model size).")
print(f"")
print(f"  {'Model':24s} {'Family':10s} {'Ctx':12s} {'MaxTok':12s} {'Size':8s} {'Pricing':18s} {'Free':5s} {'Paid':5s}")
print(f"  {'─'*24} {'─'*10} {'─'*12} {'─'*12} {'─'*8} {'─'*18} {'─'*5} {'─'*5}")
for mid in sorted(CATALOG.keys()):
    meta = CATALOG[mid]
    fam = meta["family"]
    ctx = str(meta["ctx"])
    max_tok = str(meta["max_tok"])
    size = "—"
    pricing = get_pricing(mid)
    free = is_free(mid)
    paid = not free
    free_s = f"{GREEN}yes{NC}" if free else "no"
    paid_s = "yes" if paid else f"{GREEN}—{NC}"
    print(f"  {mid:24s} {fam:10s} {ctx:12s} {max_tok:12s} {size:8s} {pricing:18s} {free_s:5s} {paid_s:5s}")

# ───────────────────────────────────────────────────────────────────────────
# Section 6 — Free vs full catalog
# ───────────────────────────────────────────────────────────────────────────
live_ids = []
for m in models:
    if isinstance(m, dict):
        mid = m.get("id") or m.get("name")
        if mid:
            live_ids.append(mid)

matched_keys = set()
for lid in live_ids:
    k, _ = catalog_lookup(lid)
    if k:
        matched_keys.add(k)

unmatched_live_ids = sorted([lid for lid in live_ids if not catalog_lookup(lid)[0]])
in_live_not_catalog = unmatched_live_ids
in_catalog_not_live = sorted(set(CATALOG.keys()) - matched_keys)

catalog_free = sorted(k for k in CATALOG if is_free(k))
catalog_paid = sorted(k for k in CATALOG if not is_free(k))

print(f"\n{CYAN}── 6. Free vs full catalog ──{NC}")
print(f"  Live total:                            {len(models)}")
print(f"  Catalog total:                         {len(CATALOG)}")
print(f"")
print(f"  Free subset (catalog pricing==0/0):    {len(catalog_free)}")
for m in catalog_free:
    print(f"    {GREEN}✓{NC} {m}")
print(f"  Paid subset:                           {len(catalog_paid)}")
print(f"")
print(f"  {YELLOW}⚠ False-positive trap (pinned by tests/test_zai_free_models.py):{NC}")
print(f"    glm-5.3-flash — name contains 'flash' but pricing is $0.15/$0.50,")
print(f"    so is_free() returns False. Pricing-derived only; no name-based heuristic.")

print(f"\n  Static ZAI_MODELS catalog size:        {len(CATALOG)}")
print(f"  In live API but NOT in catalog:        {len(in_live_not_catalog)}  (should be 0)")
for m in in_live_not_catalog[:20]:
    print(f"    + {m}")
if len(in_live_not_catalog) > 20:
    print(f"    ... + {len(in_live_not_catalog) - 20} more")
print(f"  In catalog but NOT in live API:        {len(in_catalog_not_live)}  (likely high — live is subset)")
for m in in_catalog_not_live[:20]:
    print(f"    - {m}")
if len(in_catalog_not_live) > 20:
    print(f"    ... + {len(in_catalog_not_live) - 20} more")
if not in_catalog_not_live:
    print(f"    (none — catalog is fully mirrored in live)")

# ZAI has no 'deprecation' field on cards
deprecated = [m for m in models if isinstance(m, dict) and m.get("deprecation")]
if deprecated:
    print(f"\n  {YELLOW}⚠ Deprecation warning — {len(deprecated)} live models carry a 'deprecation' field:{NC}")
    for m in deprecated:
        print(f"    ⚠ {m.get('id', '?'):40s}  deprecation={m.get('deprecation')}")
else:
    print(f"\n  No 'deprecation' field on ZAI cards (none expected — ZAI has no deprecation convention).")

print(f"\n{CYAN}{'─'*78}{NC}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_zai.json")
print(f"{CYAN}{'─'*78}{NC}")
PYEOF
