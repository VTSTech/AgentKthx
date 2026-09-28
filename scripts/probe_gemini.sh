#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_gemini.sh — Validate Gemini API Technical Reference (expanded)
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls, no tokens burned).
# Requires: GEMINI_API_KEY in env (or GOOGLE_API_KEY fallback)
#           (https://aistudio.google.com/apikey)
# Usage:    bash probe_gemini.sh
# Output:   /tmp/agentkthx_probe_gemini.json  (raw API response)
#
# Report sections:
#   1. Endpoint           — base URL, auth shape, HTTP status, raw JSON path
#   2. Static API surface  — request params, response shape, tool calling
#                            format (sourced from docs/api/GEMINI_API_
#                            TECHNICAL_REFERENCE.md; hardcoded — slow-moving)
#   3. Live /v1beta/openai/models — top-level keys, total count, sample card,
#                            family distribution, non-chat filter
#   4. Card field avail.   — which fields each live model exposes
#   5. Per-model detail     — id, family, ctx (catalog), max_tok (catalog),
#                            size (always —), pricing (always —), is_free,
#                            is_paid
#   6. Free vs full catalog — free subset, paid subset, static catalog drift,
#                            non-chat model count
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

API_KEY="${GEMINI_API_KEY:-${GOOGLE_API_KEY:-}}"
[ -z "$API_KEY" ] && echo "ERROR: Set GEMINI_API_KEY first" >&2 && exit 1

BASE_URL="${GEMINI_BASE_URL:-https://generativelanguage.googleapis.com/v1beta/openai}"

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Endpoint
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}${BOLD}═══ Gemini OpenAI-Compat API Probe ═══${NC}"
echo ""
echo -e "${CYAN}── 1. Endpoint ──${NC}"
echo "  Base URL:     ${BASE_URL}"
echo "  Models path:  /models  (OpenAI-shaped {data:[...]}; ids prefixed 'models/' — stripped)"
echo "  Auth:         Bearer \$GEMINI_API_KEY (len=${#API_KEY})"
echo "  Request:      GET ${BASE_URL}/models"

HTTP_CODE=$(curl -s -m 15 -o /tmp/agentkthx_probe_gemini.json -w "%{http_code}" \
    -H "Authorization: Bearer $API_KEY" \
    -H "User-Agent: AgentKthx-probe/0.x" \
    -H "Accept: application/json" \
    "${BASE_URL}/models")

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "  ${RED}✗ HTTP ${HTTP_CODE}${NC}"
    echo "  Response body (first 600 bytes):"
    head -c 600 /tmp/agentkthx_probe_gemini.json
    echo
    exit 1
fi
echo -e "  ${GREEN}✓ HTTP 200${NC}  (raw JSON: /tmp/agentkthx_probe_gemini.json)"

python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_gemini.json')); assert isinstance(d.get('data'), list)" 2>/dev/null || {
    echo -e "  ${RED}✗ Response is not OpenAI-shaped {\"data\": [...]}.${NC}"
    head -c 600 /tmp/agentkthx_probe_gemini.json
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

d = json.load(open("/tmp/agentkthx_probe_gemini.json"))
models = d.get("data", [])

# ───────────────────────────────────────────────────────────────────────────
# Section 2 — Static API surface (sourced from docs/api/GEMINI_API_TECHNICAL_REFERENCE.md)
# Hardcoded in the probe because the doc is slow-moving and parsing it inline
# would be brittle. Cite the doc so the source of truth is visible.
# ───────────────────────────────────────────────────────────────────────────
REQUEST_PARAMS = [
    # (name, type, required, default, notes)
    ("model",                          "string",            True,  None,       "Model id; leading 'models/' prefix accepted but stripped by backend"),
    ("messages",                       "array[Message]",    True,  None,       "Roles: system/user/assistant/tool; content may be string or multimodal array"),
    ("temperature",                    "float",             False, "1.0",      "Gemini default 1.0 (differs from OpenAI's 0.7)"),
    ("top_p",                          "float",             False, "0.95",     "Gemini default 0.95"),
    ("top_k",                          "int",               False, "40",       "GEMINI-ONLY — top-k sampling, NOT in OpenAI spec"),
    ("max_tokens",                     "int",               False, "65536",    "Output cap (OpenAI name)"),
    ("max_completion_tokens",          "int",               False, "65536",    "Output cap (newer OpenAI name) — either accepted"),
    ("stream",                         "bool",              False, "false",    "SSE stream"),
    ("stream_options",                 "object",            False, None,       "{include_usage: true}"),
    ("n",                              "int",               False, "1",        "Partial — n=1 only via OpenAI-compat; multi-candidate requires native API"),
    ("stop",                           "string | array",    False, None,       "Stop sequences"),
    ("presence_penalty",               "float",             False, "0.0",      ""),
    ("frequency_penalty",              "float",             False, "0.0",      ""),
    ("seed",                           "int",               False, None,       "Determinism seed"),
    ("reasoning_effort",               "string",            False, None,       "none|minimal|low|medium|high — mutually exclusive with thinking_config"),
    ("service_tier",                   "string",            False, "standard", "GEMINI-ONLY — standard|flex|priority"),
    ("response_format",                "object",            False, None,       "{type: text|json_object|json_schema, schema:{...}}"),
    ("tools",                          "array[Tool]",       False, None,       "OpenAI-style function-calling schemas"),
    ("tool_choice",                    "string | object",   False, "auto",     "auto|required|none OR {type:function,function:{name:X}}"),
    ("user",                           "string",            False, None,       "End-user id (passed through)"),
    ("extra_body.google.thinking_config", "object",         False, None,       "GEMINI-ONLY: {thinking_level, thinking_budget, include_thoughts, thought_signature}"),
    ("extra_body.google.cached_content", "string",          False, None,       "GEMINI-ONLY: explicit context cache id"),
    ("extra_body.google.safety_settings","array",           False, None,       "GEMINI-ONLY: per-category safety threshold overrides"),
]

RESPONSE_PARAMS = [
    # (name, type, notes)
    ("id",                             "string",            "e.g. chatcmpl-xxxxxxxxxxxx"),
    ("object",                         "string",            "'chat.completion' (or 'chat.completion.chunk' when streaming)"),
    ("created",                        "int (unix ts)",     "Response timestamp"),
    ("model",                          "string",            "Model id used"),
    ("choices",                        "array[Choice]",     "One per n (OpenAI-compat limits to n=1)"),
    ("choices[].message",              "object",            "{role:'assistant', content, reasoning?, tool_calls?}"),
    ("choices[].message.reasoning",    "string",            "GEMINI-ONLY — full thought summary when include_thoughts=true"),
    ("choices[].finish_reason",        "string",            "stop|tool_calls|length|content_filter|model_context_window_exceeded(GEMINI)"),
    ("usage",                          "object",            "{prompt_tokens, completion_tokens, total_tokens, prompt_tokens_details.cached_tokens, completion_tokens_details.reasoning_tokens}"),
    ("service_tier",                   "string",            "Echo of request service_tier"),
    ("model_version",                  "string",            "GEMINI-ONLY — fully-qualified model version (e.g. gemini-3.8-flash-001)"),
]

TOOL_CALLING = {
    "format": "OpenAI-style tools[] array (translated internally to Gemini-native functionDeclarations)",
    "tool_choice": "auto|required|none|{type:function,function:{name:X}} (maps to AUTO/ANY/NONE/specific)",
    "tool_call_id_format": "call_<alphanumeric> (OpenAI-style with 'call_' prefix)",
    "arguments_format": "JSON string (must be parsed by client)",
    "parallel_tool_calls": "Supported on Gemini 3.x — multiple tool_calls per assistant turn",
    "message_shape": '{"role":"assistant","content":"...","tool_calls":[{"id":"call_abc123","type":"function","function":{"name":"get_weather","arguments":"{\\"location\\":\\"Toronto\\"}"}}]}',
    "tool_message": '{"role":"tool","tool_call_id":"call_abc123","content":"<result>"}',
    "gemini_native_tool_types": "function (standard), code_execution, google_search, url_context, computer_use (specialized, not in chat backend)",
    "doc_ref": "docs/api/GEMINI_API_TECHNICAL_REFERENCE.md §Function Calling Implementation",
}

print(f"\n{CYAN}── 2. Static API surface{NC}  (from docs/api/GEMINI_API_TECHNICAL_REFERENCE.md)")
print(f"  {CYAN}[Request params — POST /chat/completions body]{NC}")
print(f"    {'Name':36s} {'Type':22s} {'Req':4s} {'Default':12s} Notes")
print(f"    {'─'*36} {'─'*22} {'─'*4} {'─'*12} {'─'*40}")
for name, typ, req, default, notes in REQUEST_PARAMS:
    req_s = "yes" if req else "no"
    default_s = str(default) if default is not None else "—"
    print(f"    {name:36s} {typ:22s} {req_s:4s} {default_s:12s} {notes}")

print(f"\n  {CYAN}[Response shape — chat.completion object]{NC}")
print(f"    {'Field':32s} {'Type':20s} Notes")
print(f"    {'─'*32} {'─'*20} {'─'*40}")
for name, typ, notes in RESPONSE_PARAMS:
    print(f"    {name:32s} {typ:20s} {notes}")

print(f"\n  {CYAN}[Tool calling format]{NC}")
print(f"    format:                  {TOOL_CALLING['format']}")
print(f"    tool_choice:             {TOOL_CALLING['tool_choice']}")
print(f"    tool_call_id_format:     {TOOL_CALLING['tool_call_id_format']}")
print(f"    arguments_format:        {TOOL_CALLING['arguments_format']}")
print(f"    parallel_tool_calls:     {TOOL_CALLING['parallel_tool_calls']}")
print(f"    message_shape:           {TOOL_CALLING['message_shape']}")
print(f"    tool_message:            {TOOL_CALLING['tool_message']}")
print(f"    gemini_native_tool_types: {TOOL_CALLING['gemini_native_tool_types']}")
print(f"    doc_ref:                 {TOOL_CALLING['doc_ref']}")

# ───────────────────────────────────────────────────────────────────────────
# Section 3 — Live /v1beta/openai/models response
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 3. Live /v1beta/openai/models response ──{NC}")
print(f"  Total models:           {len(models)}")
print(f"  Top-level keys:         {list(d.keys())}")
if models:
    sample = models[0]
    print(f"  Sample model object:    {sample.get('id', '?')}")
    print(f"  Sample card keys:       {list(sample.keys())}")

# Non-chat filter — substring patterns mirrored from gemini.py NON_CHAT tuple.
# Note: gemma-* is NOT filtered (chat-capable via OpenAI-compat).
NON_CHAT = ("embedding", "veo-", "lyria-", "imagen-", "robotics-", "transcribe",
            "live-translate", "-tts", "-live", "-image", "computer-use",
            "deep-research", "antigravity", "aqa", "omni-")

def strip_prefix(mid: str) -> str:
    """Strip leading 'models/' prefix used by the Gemini OpenAI-compat surface."""
    m = mid.lower()
    if m.startswith("models/"): m = m[len("models/"):]
    return m

def is_non_chat(mid: str) -> bool:
    m = strip_prefix(mid)
    return any(pat in m for pat in NON_CHAT)

chat_models = [m for m in models if not is_non_chat(m.get("id", ""))]
non_chat_models = [m for m in models if is_non_chat(m.get("id", ""))]
print(f"  Chat-capable models:    {len(chat_models)}")
print(f"  Non-chat models:        {len(non_chat_models)}  (filtered: {len(NON_CHAT)} substring patterns)")

# Model family distribution — substring bucketing (expanded from R07.03)
families = Counter()
for m in models:
    mid = strip_prefix(m.get("id", ""))
    if mid.startswith("gemini-3"):       families["gemini-3"] += 1
    elif mid.startswith("gemini-2.5"):   families["gemini-2.5"] += 1
    elif mid.startswith("gemini-2.0"):   families["gemini-2.0"] += 1
    elif mid.startswith("gemini-1"):     families["gemini-1"] += 1
    elif mid.startswith("gemma"):         families["gemma"] += 1
    elif "embedding" in mid:              families["embedding"] += 1
    elif "veo-" in mid:                   families["veo (video)"] += 1
    elif "lyria-" in mid:                 families["lyria (music)"] += 1
    elif "deep-research" in mid:          families["deep-research"] += 1
    elif "antigravity" in mid:            families["antigravity"] += 1
    elif "aqa" in mid:                     families["aqa (answer quality)"] += 1
    elif "computer-use" in mid:           families["computer-use"] += 1
    elif "-tts" in mid or "transcribe" in mid or "live-translate" in mid:
        families["live/asr/tts"] += 1
    elif "-live" in mid:                  families["live (streaming)"] += 1
    elif "imagen-" in mid or "-image" in mid:
        families["imagen (image gen)"] += 1
    elif "robotics-" in mid:              families["robotics"] += 1
    elif "omni-" in mid:                  families["omni (video)"] += 1
    else:                                  families["other"] += 1
print(f"\n  Model family distribution:  {dict(families)}")

# ───────────────────────────────────────────────────────────────────────────
# Section 4 — Card field availability (which fields each live model exposes)
# Gemini /v1beta/openai/models returns MINIMAL cards like OpenAI — only
# id/object/created/owned_by. NO context_length, NO pricing, NO capabilities.
# All rich fields ○ missing. `display_name` sometimes appears on some cards.
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 4. Card field availability (live models) ──{NC}")
STANDARD_FIELDS = ["id", "object", "created", "owned_by"]
RICH_FIELDS = [
    "display_name", "context_length", "max_context_length",
    "max_tokens", "max_output_tokens", "input_token_limit", "output_token_limit",
    "capabilities", "description", "name",
    "pricing", "size",
    "deprecation", "aliases", "default_model_temperature",
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
MAXCTX_AVAILABLE = any(m.get("context_length") or m.get("max_context_length") for m in models)
MAXTOK_AVAILABLE = any(m.get("max_tokens") or m.get("max_output_tokens") for m in models)
DEPRECATION_AVAILABLE = any("deprecation" in m for m in models)
print(f"\n  Availability summary (for per-model detail):")
print(f"    Model size available:        {'yes' if SIZE_AVAILABLE else 'no'}  ({sum(1 for m in models if m.get('size'))} models)")
print(f"    Pricing available:           {'yes' if PRICING_AVAILABLE else 'no'}  (Gemini has no pricing endpoint — catalog is source of truth)")
print(f"    Max context available:        {'yes' if MAXCTX_AVAILABLE else 'no'}  (catalog is source of truth)")
print(f"    Max tokens available:         {'yes' if MAXTOK_AVAILABLE else 'no'}  (default_max_tokens from catalog)")
print(f"    Deprecation field:            {'yes' if DEPRECATION_AVAILABLE else 'no'}  (Gemini cards have no 'deprecation' field)")

# ───────────────────────────────────────────────────────────────────────────
# Section 5 — Per-model detail
# Catalog is a SMALL fallback (10 entries) — most live models won't match.
# Free-tier rule mirrors _is_free_tier_model() in agentkthx/plugins/gemini/gemini.py:
#   Two-step: (1) FREE_TIER_LIMITS table (rpd > 0 means free);
#   (2) heuristic — paid overrides first (-pro, -image, veo-, lyria-, omni-,
#   computer-use, deep-research, gemini-2.0*, legacy aliases), then free
#   families (flash, lite, embedding, robotics, antigravity, transcribe,
#   gemma-). Default: NOT free (safer).
# ───────────────────────────────────────────────────────────────────────────
CATALOG = {
    "gemini-3.8-flash":        {"ctx": 1048576, "max_tok": 65536, "family": "gemini-3", "free_tier": True},
    "gemini-3.7-flash":        {"ctx": 1048576, "max_tok": 65536, "family": "gemini-3", "free_tier": True},
    "gemini-3.6-flash":        {"ctx": 1048576, "max_tok": 65536, "family": "gemini-3", "free_tier": True},
    "gemini-3.5-flash":        {"ctx": 1048576, "max_tok": 65536, "family": "gemini-3", "free_tier": True},
    "gemini-3.5-flash-lite":   {"ctx": 1048576, "max_tok": 65536, "family": "gemini-3", "free_tier": True},
    "gemini-3.1-flash-lite":   {"ctx": 1048576, "max_tok": 65536, "family": "gemini-3", "free_tier": True},
    "gemini-3.1-pro-preview":  {"ctx": 2097152, "max_tok": 65536, "family": "gemini-3", "free_tier": False},
    "gemini-2.5-pro":          {"ctx": 2097152, "max_tok": 65536, "family": "gemini-2.5", "free_tier": False},
    "gemini-2.5-flash":        {"ctx": 1048576, "max_tok": 65536, "family": "gemini-2.5", "free_tier": True},
    "gemini-2.5-flash-lite":   {"ctx": 1048576, "max_tok": 65536, "family": "gemini-2.5", "free_tier": True},
}

FREE_TIER_LIMITS = {
    # Free entries (rpd > 0) — sample of the ~26 free entries
    "gemini-2.5-flash":        {"rpd": 250},
    "gemini-2.5-flash-lite":   {"rpd": 250},
    "gemini-3.5-flash":        {"rpd": 250},
    "gemini-3.8-flash":        {"rpd": 250},
    "gemini-3.1-flash-lite":   {"rpd": 250},
    "gemini-embedding-001":    {"rpd": 1500},
    "gemma-3-27b-it":          {"rpd": 1500},
    # ... (~26 free entries total)
    # Paid entries (rpd = 0)
    "gemini-2.5-pro":          {"rpd": 0},
    "gemini-3.1-pro-preview":  {"rpd": 0},
}

def is_free(model_id: str) -> bool:
    """Mirror of _is_free_tier_model() in gemini.py.
    Two-step: (1) check FREE_TIER_LIMITS table (rpd > 0 means free);
    (2) heuristic — paid overrides first, then free families.
    Default: NOT free (safer)."""
    m = strip_prefix(model_id)
    # 1. Exact match
    if m in FREE_TIER_LIMITS:
        return FREE_TIER_LIMITS[m]["rpd"] > 0
    # 2. Paid overrides
    if "-pro" in m or "-pro-preview" in m or "-pro-image" in m: return False
    if "-image" in m or "imagen-" in m: return False
    if "veo-" in m or "lyria-" in m or "omni-" in m: return False
    if "computer-use" in m or "deep-research" in m: return False
    if m.startswith("gemini-2.0") or m == "gemini-flash-latest" or m == "gemini-pro-latest": return False
    # 3. Free families
    if "flash" in m: return True
    if "lite" in m: return True
    if "embedding" in m: return True
    if "gemma-" in m: return True
    # 4. Default
    return False

def family_of(model_id: str) -> str:
    m = strip_prefix(model_id)
    if m.startswith("gemini-3"):       return "gemini-3"
    if m.startswith("gemini-2.5"):     return "gemini-2.5"
    if m.startswith("gemini-2.0"):     return "gemini-2.0"
    if m.startswith("gemini-1"):       return "gemini-1"
    if m.startswith("gemma-"):         return "gemma"
    if "embedding" in m:               return "embedding"
    if "veo-" in m:                    return "veo"
    if "lyria-" in m:                  return "lyria"
    if "imagen-" in m or "-image" in m: return "imagen"
    if "deep-research" in m:           return "deep-research"
    if "antigravity" in m:             return "antigravity"
    if "computer-use" in m:            return "computer-use"
    if "robotics-" in m:               return "robotics"
    if "omni-" in m:                   return "omni"
    if "aqa" in m:                     return "aqa"
    if "transcribe" in m or "-tts" in m or "live-translate" in m:
        return "live/asr/tts"
    if "-live" in m:                   return "live"
    return "other"

def catalog_key(live_id: str) -> str:
    """Strip 'models/' prefix and '-preview-NN-YYYY' / '-NN-YYYY' date suffixes."""
    lid = strip_prefix(live_id)
    lid = re.sub(r"-preview-\d+-\d{4}$", "", lid)
    lid = re.sub(r"-\d{4}$", "", lid)
    return lid

def catalog_lookup(live_id: str):
    """Try catalog with: (1) catalog_key as-is, (2) +'-latest' fallback.
       Returns (catalog_key_or_None, catalog_meta_or_None)."""
    k = catalog_key(live_id)
    if k in CATALOG: return k, CATALOG[k]
    # Gemini doesn't use -latest suffix convention like Mistral, but try anyway
    k2 = k + "-latest"
    if k2 in CATALOG: return k2, CATALOG[k2]
    return None, None

def get_ctx(model_id: str, m_card: dict) -> str:
    live = m_card.get("context_length") or m_card.get("max_context_length")
    if live: return str(live)
    _, meta = catalog_lookup(model_id)
    if meta and meta.get("ctx"):
        return f"{meta['ctx']}(cat)"
    return "—"

def get_max_tok(model_id: str) -> str:
    _, meta = catalog_lookup(model_id)
    if meta and meta.get("max_tok"):
        return f"{meta['max_tok']}(cat)"
    return "—"

def get_size(m_card: dict) -> str:
    # Gemini cards never carry a 'size' field — always —
    return "—"

def get_pricing(model_id: str) -> str:
    # Gemini has no pricing endpoint — always —
    return "—"

print(f"\n{CYAN}── 5. Per-model detail ──{NC}")
print(f"  (Free rule: FREE_TIER_LIMITS table (rpd > 0) OR heuristic: flash/lite/embedding/gemma- families)")
print(f"  Catalog is a SMALL fallback (10 entries) — most live models classified via heuristic only.")
print(f"  Live API exposes no pricing/size/context_length/max_tokens — '—' or '(cat)' from catalog.")
print(f"")
print(f"  {'Model':42s} {'Family':14s} {'Ctx':14s} {'MaxTok':14s} {'Size':6s} {'Pricing':8s} {'Free':5s} {'Paid':5s}")
print(f"  {'─'*42} {'─'*14} {'─'*14} {'─'*14} {'─'*6} {'─'*8} {'─'*5} {'─'*5}")
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
    print(f"  {mid:42s} {fam:14s} {ctx:14s} {max_tok:14s} {size:6s} {pricing:8s} {free_s:5s} {paid_s:5s}")

# ───────────────────────────────────────────────────────────────────────────
# Section 6 — Free vs full catalog
# ───────────────────────────────────────────────────────────────────────────
free_models = [m.get("id", "?") for m in models if is_free(m.get("id", ""))]
paid_models = [m.get("id", "?") for m in models if not is_free(m.get("id", ""))]

print(f"\n{CYAN}── 6. Free vs full catalog ──{NC}")
print(f"  Live total:               {len(models)}")
print(f"  Chat-capable live:       {len(chat_models)}  (NON_CHAT filter applied)")
print(f"  Non-chat live:           {len(non_chat_models)}  (cannot serve as chat backends)")
print(f"  Free (is_free=yes):      {len(free_models)}")
if free_models:
    for m in free_models[:15]:
        print(f"    {GREEN}✓{NC} {m}")
    if len(free_models) > 15:
        print(f"    ... + {len(free_models) - 15} more")
else:
    print(f"    (none — no flash/lite/embedding/gemma- models surfaced)")
print(f"  Paid (is_paid=yes):      {len(paid_models)}")
if paid_models:
    for m in paid_models[:5]:
        print(f"    · {m}")
    if len(paid_models) > 5:
        print(f"    ... + {len(paid_models) - 5} more")

# Catalog drift — note: catalog is intentionally a SMALL fallback (10 entries).
# Live API exposes 40+ models; most will be classified via heuristic, NOT catalog.
# Report unmatched live models as "→ classified via heuristic, not catalog"
# rather than as drift alarms.
matched_keys = set()
unmatched_via_heuristic = []
for m in models:
    if not m.get("id"): continue
    k, _ = catalog_lookup(m["id"])
    if k:
        matched_keys.add(k)
    else:
        unmatched_via_heuristic.append(m["id"])

in_live_not_catalog = sorted(unmatched_via_heuristic)
in_catalog_not_live = sorted(set(CATALOG.keys()) - matched_keys)

print(f"\n  Static GEMINI_MODELS catalog size:  {len(CATALOG)}")
print(f"  Catalog is a small fallback — most live models classified via heuristic, not catalog.")
print(f"  In live API, matched by catalog:            {len(matched_keys)}")
print(f"  In live API, NOT in catalog (heuristic-only):  {len(in_live_not_catalog)}")
for m in in_live_not_catalog[:15]:
    free_flag = "free" if is_free(m) else "paid"
    print(f"    + {m:50s}  ({free_flag} — classified via heuristic, not catalog)")
if len(in_live_not_catalog) > 15:
    print(f"    ... + {len(in_live_not_catalog) - 15} more")
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
for m in catalog_paid:
    print(f"    · {m}")

# Gemini has no 'deprecation' field on cards (unlike Mistral) — confirm.
print(f"\n  Deprecation field on cards:  {'yes' if DEPRECATION_AVAILABLE else 'no'}")
print(f"    (Gemini cards have no 'deprecation' field; model lifecycle tracked externally)")

print(f"\n{CYAN}{'─'*78}{NC}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_gemini.json")
print(f"{CYAN}{'─'*78}{NC}")
PYEOF
