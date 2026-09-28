#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_openrouter.sh — Validate OpenRouter API Technical Reference (expanded)
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls, no tokens burned).
# OPENROUTER_API_KEY OPTIONAL — /models is anonymous; only /credits or chat
#                               endpoints require auth.
# Usage:    bash probe_openrouter.sh
# Output:   /tmp/agentkthx_probe_openrouter.json  (raw API response)
#
# Report sections:
#   1. Endpoint           — base URL, auth shape (anonymous OK), HTTP status,
#                            raw JSON path
#   2. Static API surface  — request params, response shape, tool calling
#                            format (sourced from docs/api/OPENROUTER_API_
#                            TECHNICAL_REFERENCE.md; hardcoded — slow-moving)
#   3. Live /v1/models     — top-level keys, total count, sample card
#   4. Card field avail.   — which fields each live model exposes
#   5. Per-model detail     — id, family, max context, max tokens, size avail,
#                            pricing avail, is_free, is_paid
#   6. Free vs full catalog — free subset, paid subset, static catalog drift,
#                            top-5 cheapest / top-5 longest context bonus lists
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

API_KEY="${OPENROUTER_API_KEY:-}"
BASE_URL="${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}"

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Endpoint
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}${BOLD}═══ OpenRouter API Probe ═══${NC}"
echo ""
echo -e "${CYAN}── 1. Endpoint ──${NC}"
echo "  Base URL:     ${BASE_URL}"
echo "  Models path:  /models  (anonymous — no auth required)"
if [ -n "$API_KEY" ]; then
    echo "  Auth:         Bearer \$OPENROUTER_API_KEY (len=${#API_KEY})"
    AUTH_MODE="bearer"
else
    echo "  Auth:         anonymous (OPENROUTER_API_KEY unset — /models is anonymous)"
    AUTH_MODE="anonymous"
fi
echo "  Attribution:  HTTP-Referer: https://github.com/VTSTech/AgentKthx"
echo "                X-Title: AgentKthx"
echo "  Request:      GET ${BASE_URL}/models"

# Build curl header args — auth is OPTIONAL for /models, but include attribution
# headers (OpenRouter docs recommend them for ranking/leaderboard credit).
CURL_HEADERS=(
    -H "User-Agent: AgentKthx-probe/0.x"
    -H "Accept: application/json"
    -H "HTTP-Referer: https://github.com/VTSTech/AgentKthx"
    -H "X-Title: AgentKthx"
)
if [ -n "$API_KEY" ]; then
    CURL_HEADERS+=(-H "Authorization: Bearer $API_KEY")
fi

# curl -w "%{http_code}" writes the HTTP code as the last thing on stdout.
# If the connection fails entirely (DNS/timeout), curl exits non-zero and
# HTTP_CODE may be empty — coerce to "000" so the != "200" branch fires.
HTTP_CODE=$(curl -s -m 15 -o /tmp/agentkthx_probe_openrouter.json -w "%{http_code}" \
    "${CURL_HEADERS[@]}" \
    "${BASE_URL}/models" 2>/dev/null) || HTTP_CODE="000"
[ -z "$HTTP_CODE" ] && HTTP_CODE="000"

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "  ${RED}✗ HTTP ${HTTP_CODE}${NC}"
    if [ -s /tmp/agentkthx_probe_openrouter.json ]; then
        echo "  Response body (first 600 bytes):"
        head -c 600 /tmp/agentkthx_probe_openrouter.json
        echo
    else
        echo "  (empty response body — likely network failure, DNS issue, or timeout)"
    fi
    exit 1
fi
echo -e "  ${GREEN}✓ HTTP 200${NC}  (auth: ${AUTH_MODE})  (raw JSON: /tmp/agentkthx_probe_openrouter.json)"

python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_openrouter.json')); assert isinstance(d.get('data'), list)" 2>/dev/null || {
    echo -e "  ${RED}✗ Response is not OpenAI-shaped {\"data\": [...]}.${NC}"
    head -c 600 /tmp/agentkthx_probe_openrouter.json
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

d = json.load(open("/tmp/agentkthx_probe_openrouter.json"))
models = d.get("data", [])

# ───────────────────────────────────────────────────────────────────────────
# Section 2 — Static API surface (sourced from docs/api/OPENROUTER_API_TECHNICAL_REFERENCE.md)
# Hardcoded in the probe because the doc is slow-moving and parsing it inline
# would be brittle. Cite the doc so the source of truth is visible.
# ───────────────────────────────────────────────────────────────────────────
REQUEST_PARAMS = [
    # (name, type, required, default, notes)
    ("model",              "string",            True,  None,    "Model id from /models; may carry :free/:fastest/:cheapest suffix"),
    ("messages",           "array[Message]",    True,  None,    "Roles: system/user/assistant/tool; content may be string or array"),
    ("temperature",        "float",             False, None,    "Range 0.0-2.0"),
    ("top_p",              "float",             False, "1.0",   "Nucleus sampling"),
    ("top_k",              "int",               False, "0",     "Top-K"),
    ("top_a",              "float",             False, "0",     "OPENROUTER-EXTENDED"),
    ("min_p",              "float",             False, "0",     "OPENROUTER-EXTENDED — newer nucleus alt"),
    ("seed",               "int",               False, None,    "Best-effort; not all providers honor"),
    ("max_tokens",         "int",               False, None,    "Preferred over max_completion_tokens for compat"),
    ("max_completion_tokens","int",             False, None,    "OpenAI newer name; many free/3rd-party providers don't support"),
    ("stop",               "list[str]",         False, None,    "Up to 4 strings"),
    ("n",                  "int",               False, "1",     "Most providers cap at 1"),
    ("presence_penalty",   "float",             False, "0.0",   "Range -2.0-2.0"),
    ("frequency_penalty",  "float",             False, "0.0",   "Range -2.0-2.0"),
    ("repetition_penalty", "float",             False, "1.0",   "OPENROUTER-EXTENDED — Llama-family"),
    ("logit_bias",         "dict",              False, "{}",    "token_id -> bias"),
    ("logprobs",           "bool",              False, "false", ""),
    ("top_logprobs",       "int 0-20",          False, None,    "Requires logprobs:true"),
    ("stream",             "bool",              False, "false", "SSE stream"),
    ("stream_options",     "object",            False, None,    "{include_usage: true}"),
    ("user",               "string",            False, None,    "End-user id"),
    ("response_format",    "object",            False, None,    "{type: text|json_object|json_schema, schema?}"),
    ("tools",              "array[Tool]",       False, None,    "OpenAI function-calling schemas"),
    ("tool_choice",        "str|object",        False, "auto",  "auto|none|required|{type:function,function:{name:X}}"),
    ("reasoning",          "object",            False, None,    "OPENROUTER-UNIQUE: {effort:low|medium|high, max_tokens?, exclude?}"),
    ("transforms",         "array[str]",        False, None,    "OPENROUTER-UNIQUE: ['middle-out'] for auto-truncation"),
    ("plugins",            "array[object]",     False, None,    "OPENROUTER-UNIQUE: [{id:'web', max_results:3}] for web search"),
    ("provider",           "object",            False, None,    "OPENROUTER-UNIQUE: routing prefs {order, allow_fallbacks, require_parameters, ignore, quantizations, data_collection}"),
]

RESPONSE_PARAMS = [
    # (name, type, notes)
    ("id",                 "string",            "e.g. gen-1234567890"),
    ("provider",           "string",            "OPENROUTER-UNIQUE — upstream that served the request (e.g. 'Anthropic', 'Together')"),
    ("model",              "string",            "Model id used"),
    ("object",             "string",            "'chat.completion'"),
    ("created",            "int (unix ts)",     "Response timestamp"),
    ("choices",            "array[Choice]",     "One per n"),
    ("choices[].message",  "object",            "{role:'assistant', content, reasoning?, reasoning_content?, tool_calls?}"),
    ("choices[].finish_reason", "string",       "stop|tool_calls|length|content_filter|model_context_window_exceeded(OR-unique)"),
    ("usage",              "object",            "{prompt_tokens, completion_tokens, total_tokens, cost(OR-unique, USD spent)}"),
    ("error",              "object (optional)", "OPENROUTER-UNIQUE — provider-side error on HTTP 200 (upstream rate-limited)"),
]

TOOL_CALLING = {
    "format": "OpenAI-style tools[] array (function-calling convention)",
    "tool_choice": "auto|none|required|{type:function,function:{name:X}}",
    "tool_call_id_format": "call_<alphanumeric> (OpenAI-style with 'call_' prefix)",
    "arguments_format": "JSON string (must be parsed by client)",
    "parallel_tool_calls": "bool, default true",
    "message_shape": '{"role":"assistant","content":"...","tool_calls":[{"id":"call_123","type":"function","function":{"name":"get_weather","arguments":"{\\"city\\":\\"NYC\\"}"}}]}',
    "tool_message": '{"role":"tool","content":"result","tool_call_id":"call_123","name":"get_weather"}',
    "react_fallback": "400/422 'does not support tools' -> backend retries without tools field (ReAct fallback)",
    "plugins_web_search": "plugins:[{id:'web'}] causes web_search results to come back as a tool_calls entry",
    "doc_ref": "docs/api/OPENROUTER_API_TECHNICAL_REFERENCE.md §Function Calling Implementation",
}

print(f"\n{CYAN}── 2. Static API surface{NC}  (from docs/api/OPENROUTER_API_TECHNICAL_REFERENCE.md)")
print(f"  {CYAN}[Request params — POST /chat/completions body]{NC}")
print(f"    {'Name':26s} {'Type':22s} {'Req':4s} {'Default':14s} Notes")
print(f"    {'─'*26} {'─'*22} {'─'*4} {'─'*14} {'─'*40}")
for name, typ, req, default, notes in REQUEST_PARAMS:
    req_s = "yes" if req else "no"
    default_s = str(default) if default is not None else "—"
    print(f"    {name:26s} {typ:22s} {req_s:4s} {default_s:14s} {notes}")

print(f"\n  {CYAN}[Response shape — chat.completion object]{NC}")
print(f"    {'Field':28s} {'Type':22s} Notes")
print(f"    {'─'*28} {'─'*22} {'─'*40}")
for name, typ, notes in RESPONSE_PARAMS:
    print(f"    {name:28s} {typ:22s} {notes}")

print(f"\n  {CYAN}[Tool calling format]{NC}")
print(f"    format:              {TOOL_CALLING['format']}")
print(f"    tool_choice:         {TOOL_CALLING['tool_choice']}")
print(f"    tool_call_id_format: {TOOL_CALLING['tool_call_id_format']}")
print(f"    arguments_format:    {TOOL_CALLING['arguments_format']}")
print(f"    parallel_tool_calls: {TOOL_CALLING['parallel_tool_calls']}")
print(f"    message_shape:       {TOOL_CALLING['message_shape']}")
print(f"    tool_message:        {TOOL_CALLING['tool_message']}")
print(f"    react_fallback:      {TOOL_CALLING['react_fallback']}")
print(f"    plugins_web_search:  {TOOL_CALLING['plugins_web_search']}")
print(f"    doc_ref:             {TOOL_CALLING['doc_ref']}")

# ───────────────────────────────────────────────────────────────────────────
# Section 3 — Live /models response
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 3. Live /models response ──{NC}")
print(f"  Total models:           {len(models)}")
print(f"  Top-level keys:         {list(d.keys())}")
if models:
    sample = models[0]
    print(f"  Sample model object:    {sample.get('id', '?')}")
    print(f"  Sample card keys:       {list(sample.keys())}")

# ───────────────────────────────────────────────────────────────────────────
# Section 4 — Card field availability (which fields each live model exposes)
# OpenRouter cards are RICH — much richer than OpenAI/Mistral. Most fields are
# present on all live models; a few (per_request_limits, is_free) are partial.
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 4. Card field availability (live models) ──{NC}")
# Standard OpenAI fields — only 'id' is reliably present on OpenRouter cards.
STANDARD_FIELDS = ["id", "object", "created", "owned_by"]
# Rich OpenRouter-specific top-level fields.
RICH_FIELDS = [
    "name", "description", "context_length", "architecture",
    "pricing", "top_provider", "per_request_limits",
    "supported_parameters", "is_free",
]
# Fields NOT exposed by OpenRouter cards (probe should note the gap).
NOT_EXPOSED = [
    "size", "max_tokens (top-level — use top_provider.max_completion_tokens)",
    "default_model_temperature", "aliases", "deprecation",
    "capabilities", "max_context_length (it's 'context_length' here)",
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

# Nested-object field availability (architecture.*, pricing.*, top_provider.*)
print(f"\n  Nested-object field availability:")
arch_fields = ["modality", "input_modalities", "output_modalities", "tokenizer"]
for f in arch_fields:
    n = sum(1 for m in models if f in (m.get("architecture") or {}))
    pct = (100.0 * n / len(models)) if models else 0
    print(f"    architecture.{f:20s}  {n}/{len(models)}  ({pct:.0f}%)")
pricing_fields = ["prompt", "completion", "image", "request", "web_search"]
for f in pricing_fields:
    n = sum(1 for m in models if f in (m.get("pricing") or {}))
    pct = (100.0 * n / len(models)) if models else 0
    print(f"    pricing.{f:24s}      {n}/{len(models)}  ({pct:.0f}%)")
tp_fields = ["context_length", "max_completion_tokens", "provider", "is_moderated"]
for f in tp_fields:
    n = sum(1 for m in models if f in (m.get("top_provider") or {}))
    pct = (100.0 * n / len(models)) if models else 0
    print(f"    top_provider.{f:22s}   {n}/{len(models)}  ({pct:.0f}%)")

# Non-chat filter note — OpenRouter /models only returns chat-capable models.
print(f"\n  Non-chat filter: NONE — OpenRouter /models only returns chat-capable")
print(f"  models (no embeddings/vision/audio endpoint to filter out). The")
print(f"  is_chat_model flag in openrouter.py defaults True and only overrides")
print(f"  when architecture.modality explicitly omits 'text'.")

# Availability summary for per-model detail
SIZE_AVAILABLE = any(m.get("size") for m in models)  # always False — OpenRouter doesn't expose
PRICING_AVAILABLE = any("pricing" in m for m in models)
MAXCTX_AVAILABLE = any(m.get("context_length") for m in models)
MAXTOK_AVAILABLE = any((m.get("top_provider") or {}).get("max_completion_tokens") for m in models)
print(f"\n  Availability summary (for per-model detail):")
print(f"    Model size available:  {'yes' if SIZE_AVAILABLE else 'no'}  (OpenRouter cards do not expose size — always '—')")
print(f"    Pricing available:     {'yes' if PRICING_AVAILABLE else 'no'}  (live API is source of truth — per-token USD strings)")
print(f"    Max context available: {'yes' if MAXCTX_AVAILABLE else 'no'}  (live 'context_length' field)")
print(f"    Max tokens available:  {'yes' if MAXTOK_AVAILABLE else 'no'}  (live 'top_provider.max_completion_tokens', often null)")

# Fields NOT exposed
print(f"\n  Fields NOT exposed on OpenRouter cards:")
for f in NOT_EXPOSED:
    print(f"    ✗ {f}")

# ───────────────────────────────────────────────────────────────────────────
# Section 5 — Per-model detail
# Catalog is a small FALLBACK (18 entries) — most live models are
# router-discovered, not catalog-pinned. Live API is the source of truth for
# pricing, ctx, and max_tok. Catalog pricing is $/1M tokens; live API pricing
# is per-token USD strings — we normalize live to per-1M for display.
#
# Free-tier rule mirrors _is_free_model() + parse-time triplet in openrouter.py:
#   1. model_id == "openrouter/free" (the named Free Models Router)
#   2. model_id.endswith(":free") (OpenRouter's canonical free marker)
#   3. live pricing.prompt == 0.0 AND pricing.completion == 0.0 (ground truth)
# ───────────────────────────────────────────────────────────────────────────
CATALOG = {
    "anthropic/claude-3.5-sonnet":      {"ctx": 131072,  "max_tok": 131072,  "in": 15.00, "out": 75.00, "provider": "anthropic"},
    "anthropic/claude-3.5-haiku":       {"ctx": 200000,  "max_tok": 8192,    "in": 0.80,  "out": 4.00,  "provider": "anthropic"},
    "anthropic/claude-3-haiku":         {"ctx": 131072,  "max_tok": 131072,  "in": 0.25,  "out": 1.25,  "provider": "anthropic"},
    "anthropic/claude-3-opus":          {"ctx": 200000,  "max_tok": 4096,    "in": 15.00, "out": 75.00, "provider": "anthropic"},
    "openai/gpt-4o":                    {"ctx": 128000,  "max_tok": 128000,  "in": 2.50,  "out": 10.00, "provider": "openai"},
    "openai/gpt-4o-mini":               {"ctx": 128000,  "max_tok": 16384,   "in": 0.15,  "out": 0.60,  "provider": "openai"},
    "openai/gpt-4":                     {"ctx": 8192,    "max_tok": 8192,    "in": 30.00, "out": 60.00, "provider": "openai"},
    "openai/gpt-3.5-turbo":             {"ctx": 16385,   "max_tok": 4096,    "in": 0.50,  "out": 1.50,  "provider": "openai"},
    "deepseek/deepseek-chat":           {"ctx": 131072,  "max_tok": 131072,  "in": 1.00,  "out": 2.00,  "provider": "deepseek"},
    "deepseek/deepseek-r1":             {"ctx": 65536,   "max_tok": 65536,   "in": 0.55,  "out": 2.19,  "provider": "deepseek"},
    "google/gemini-1.5-flash":          {"ctx": 2097152, "max_tok": 2097152, "in": 0.075, "out": 0.30,  "provider": "google"},
    "google/gemini-1.5-pro":            {"ctx": 2097152, "max_tok": 2097152, "in": 12.50, "out": 50.00, "provider": "google"},
    "cohere/command-r-plus":            {"ctx": 131072,  "max_tok": 131072,  "in": 3.00,  "out": 15.00, "provider": "cohere"},
    "cohere/command-r":                 {"ctx": 128000,  "max_tok": 128000,  "in": 0.15,  "out": 0.60,  "provider": "cohere"},
    "meta-llama/llama-3.1-70b-instruct":{"ctx": 131072,  "max_tok": 131072,  "in": 0.88,  "out": 0.88,  "provider": "meta"},
    "meta-llama/llama-3.1-8b-instruct":{"ctx": 131072,  "max_tok": 131072,  "in": 0.05,  "out": 0.05,  "provider": "meta"},
    "qwen/qwen-2.5-72b-instruct":       {"ctx": 131072,  "max_tok": 131072,  "in": 0.50,  "out": 0.50,  "provider": "qwen"},
    "mistralai/mixtral-8x7b-instruct":  {"ctx": 32768,   "max_tok": 32768,   "in": 0.24,  "out": 0.24,  "provider": "mistralai"},
}

# Mirror of _is_free_model() in openrouter.py + the parse-time triplet.
# Three signals OR-combined:
#   1. model_id in OPENROUTER_FREE_MODEL_WHITELIST (the named Free Models Router)
#   2. model_id.endswith(":free") (OpenRouter's canonical free marker)
#   3. live pricing.prompt == 0.0 AND pricing.completion == 0.0
# Signal 3 is the ground truth — overrides the others.
OPENROUTER_FREE_MODEL_WHITELIST = frozenset({"openrouter/free"})

def is_free(model_id, live_card=None):
    if model_id in OPENROUTER_FREE_MODEL_WHITELIST:
        return True
    if model_id.endswith(":free"):
        return True
    if live_card:
        pricing = live_card.get("pricing", {}) or {}
        try:
            p = float(pricing.get("prompt", 0) or 0)
            c = float(pricing.get("completion", 0) or 0)
        except (TypeError, ValueError):
            return False
        if p == 0.0 and c == 0.0:
            return True
    return False

def catalog_key(live_id):
    """OpenRouter ids are canonical provider/model form. No date-suffix
    stripping or alias normalization needed — identity map."""
    return live_id

def family_of(model_id, m_card):
    """Prefer top_provider.provider (the actual upstream that served the
    request); fall back to id.split('/')[0]."""
    tp = m_card.get("top_provider", {}) or {}
    p = tp.get("provider")
    if p:
        return p
    if "/" in model_id:
        return model_id.split("/", 1)[0]
    return "?"

def get_ctx(m_card):
    """Live 'context_length' field."""
    v = m_card.get("context_length")
    return str(v) if v else "—"

def get_max_tok(m_card):
    """Live 'top_provider.max_completion_tokens'; frequently null."""
    tp = m_card.get("top_provider", {}) or {}
    v = tp.get("max_completion_tokens")
    return str(v) if v else "—"

def get_size(m_card):
    """OpenRouter cards do NOT expose model size."""
    return "—"

def get_pricing(m_card):
    """Live per-token USD strings × 1M = per-1M USD for display.
    Catalog prices are already per-1M; live prices need normalization.
    Format: $X.XX/$Y.YY(live)."""
    pr = m_card.get("pricing", {}) or {}
    try:
        p_in = float(pr.get("prompt", 0) or 0) * 1_000_000
        p_out = float(pr.get("completion", 0) or 0) * 1_000_000
    except (TypeError, ValueError):
        return "—"
    return f"${p_in:.2f}/${p_out:.2f}(live)"

print(f"\n{CYAN}── 5. Per-model detail ──{NC}")
print(f"  (Free rule: id=='openrouter/free' OR id.endswith(':free') OR live pricing==0/0)")
print(f"  Live pricing is per-token USD; normalized to per-1M USD for display (× 1,000,000).")
print(f"  Showing first 40 models alphabetically — see raw JSON for the full {len(models)}-model list.")
print(f"")
print(f"  {'Model':44s} {'Family':16s} {'Ctx':10s} {'MaxTok':10s} {'Size':6s} {'Pricing':22s} {'Free':5s} {'Paid':5s}")
print(f"  {'─'*44} {'─'*16} {'─'*10} {'─'*10} {'─'*6} {'─'*22} {'─'*5} {'─'*5}")
sorted_models = sorted(models, key=lambda x: x.get("id", ""))
SHOW_N = 40
for m in sorted_models[:SHOW_N]:
    mid = m.get("id", "?")
    fam = family_of(mid, m)
    ctx = get_ctx(m)
    max_tok = get_max_tok(m)
    size = get_size(m)
    pricing = get_pricing(m)
    free = is_free(mid, m)
    paid = not free
    free_s = f"{GREEN}yes{NC}" if free else "no"
    paid_s = "yes" if paid else f"{GREEN}—{NC}"
    print(f"  {mid:44s} {fam:16s} {ctx:10s} {max_tok:10s} {size:6s} {pricing:22s} {free_s:5s} {paid_s:5s}")
if len(sorted_models) > SHOW_N:
    print(f"  ... + {len(sorted_models) - SHOW_N} more (see /tmp/agentkthx_probe_openrouter.json)")

# ───────────────────────────────────────────────────────────────────────────
# Section 6 — Free vs full catalog
# ───────────────────────────────────────────────────────────────────────────
free_models = [m.get("id", "?") for m in models if is_free(m.get("id", ""), m)]
paid_models = [m.get("id", "?") for m in models if not is_free(m.get("id", ""), m)]

print(f"\n{CYAN}── 6. Free vs full catalog ──{NC}")
print(f"  Live total:              {len(models)}")
print(f"  Free (is_free=yes):      {len(free_models)}")
if free_models:
    for m in free_models[:15]:
        print(f"    {GREEN}✓{NC} {m}")
    if len(free_models) > 15:
        print(f"    ... + {len(free_models) - 15} more (most are ':free' suffix variants of paid models)")
else:
    print(f"    (none — no 'openrouter/free' router, no ':free' suffix, no zero-pricing models)")
print(f"  Paid (is_paid=yes):      {len(paid_models)}")
if paid_models:
    for m in paid_models[:5]:
        print(f"    · {m}")
    if len(paid_models) > 5:
        print(f"    ... + {len(paid_models) - 5} more")

# Catalog drift — OpenRouter live API has 300+ models; catalog has 18.
# MOST live models won't match the catalog — that's EXPECTED. The catalog is
# a small fallback (used when /models is unreachable), not the source of truth.
matched_keys = set()
for m in models:
    mid = m.get("id", "")
    if not mid:
        continue
    k = catalog_key(mid)
    if k in CATALOG:
        matched_keys.add(k)
in_live_not_catalog = sorted({
    m.get("id", "") for m in models
    if m.get("id") and catalog_key(m["id"]) not in CATALOG
})
in_catalog_not_live = sorted(set(CATALOG.keys()) - matched_keys)

print(f"\n  Static OPENROUTER_MODELS catalog size:  {len(CATALOG)}  (small fallback)")
print(f"  In live API but NOT in catalog:          {len(in_live_not_catalog)}")
print(f"    (EXPECTED — catalog is a small fallback (18 entries); most live models are")
print(f"     router-discovered, not catalog-pinned. Live API is source of truth.)")
if in_live_not_catalog:
    print(f"    Sample of unmatched live ids (first 20):")
    for m in in_live_not_catalog[:20]:
        print(f"      + {m}")
    if len(in_live_not_catalog) > 20:
        print(f"      ... + {len(in_live_not_catalog) - 20} more")
print(f"  In catalog but NOT in live API:          {len(in_catalog_not_live)}")
for m in in_catalog_not_live:
    print(f"    - {m}")
if not in_catalog_not_live:
    print(f"    (none — all {len(CATALOG)} catalog entries matched live API)")

# Catalog free subset — catalog has NO :free-suffixed entries by design;
# every catalog entry is paid. Free models come from the live API only.
catalog_free = sorted(k for k in CATALOG if is_free(k))
catalog_paid = sorted(k for k in CATALOG if not is_free(k))
print(f"\n  Catalog free subset:                    {len(catalog_free)}  (catalog has no :free entries by design)")
print(f"  Catalog paid subset:                    {len(catalog_paid)}")
for m in catalog_paid[:10]:
    meta = CATALOG[m]
    print(f"    · {m:44s}  ${meta['in']:.2f}/${meta['out']:.2f} per 1M, ctx={meta['ctx']}")
if len(catalog_paid) > 10:
    print(f"    ... + {len(catalog_paid) - 10} more")

# Deprecation field early-warning — OpenRouter cards do NOT expose 'deprecation'.
deprecated = [m for m in models if m.get("deprecation")]
print(f"\n  Deprecation field: NOT exposed on OpenRouter cards.")
if deprecated:
    print(f"    {YELLOW}⚠ Unexpected: {len(deprecated)} live models carry a 'deprecation' field:{NC}")
    for m in deprecated:
        print(f"      ⚠ {m.get('id', '?'):40s}  deprecation={m.get('deprecation')}")
else:
    print(f"    (None of the {len(models)} live models expose a 'deprecation' field.)")
    print(f"    OpenRouter surfaces deprecation via the model description text or by")
    print(f"    removing the model from /models entirely. No early-warning field.")

# Bonus subsections (preserved from pre-expanded probe):
# Top 5 cheapest input rates (paid models only, normalized to per-1M USD)
priced = []
for m in models:
    pr = m.get("pricing", {}) or {}
    try:
        p_in = float(pr.get("prompt", 0) or 0)
        p_out = float(pr.get("completion", 0) or 0)
    except (TypeError, ValueError):
        continue
    if p_in > 0 or p_out > 0:  # exclude free (zero-pricing) models
        priced.append((m.get("id", "?"), p_in * 1_000_000, p_out * 1_000_000))
priced.sort(key=lambda x: x[1])
print(f"\n  Top 5 cheapest input rates (paid models, USD per 1M tokens):")
for mid, in_r, out_r in priced[:5]:
    print(f"    {mid:55s}  in=${in_r:.4f}/1M  out=${out_r:.4f}/1M")

# Top 5 longest context
ctx_list = [(m.get("id", "?"), m.get("context_length", 0) or 0) for m in models]
ctx_list.sort(key=lambda x: x[1], reverse=True)
print(f"\n  Top 5 longest context:")
for mid, ctx in ctx_list[:5]:
    print(f"    {mid:55s}  {ctx:>10,} tokens")

print(f"\n{CYAN}{'─'*78}{NC}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_openrouter.json")
print(f"{CYAN}{'─'*78}{NC}")
PYEOF
