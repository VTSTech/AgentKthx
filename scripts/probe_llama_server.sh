#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_llama_server.sh — Validate llama-server Backend (expanded)
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (local backend; no inference calls, no tokens burned).
# Requires: llama-server running on localhost:8764 (or LLAMA_SERVER_BASE_URL set)
# Usage:    bash probe_llama_server.sh
# Output:   /tmp/agentkthx_probe_llama_server.json        (raw /v1/models body)
#           /tmp/agentkthx_probe_llama_server_slots.json  (raw /slots body)
#
# Report sections:
#   1. Endpoint           — base URL, auth shape (NONE — local), HTTP status,
#                            raw JSON path
#   2. Static API surface  — request params, response shape, tool calling
#                            format (sourced from agentkthx/backends/
#                            llama_server.py; hardcoded — slow-moving)
#   3. Live /v1/models     — top-level keys, total count, sample card
#   4. Card field avail.   — which fields each live model exposes
#                            (OpenAI shape + llama-server `meta` extension)
#   5. Per-model detail     — id, family, runtime ctx (meta.n_ctx) +
#                            trained ctx (meta.n_ctx_train), max_tok (—),
#                            size (meta.size in MB), pricing=N/A,
#                            is_free=N/A, is_paid=N/A  (LOCAL backend)
#   6. Loaded vs available  — loaded model count, active /slots state
#                            (slot id, busy/idle, n_past, n_ctx); /slots
#                            is NOT used by AgentKthx backend — net-new probe
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

BASE_URL="${LLAMA_SERVER_BASE_URL:-http://localhost:8764}"

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Endpoint
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}${BOLD}═══ llama-server Backend Probe ═══${NC}"
echo ""
echo -e "${CYAN}── 1. Endpoint ──${NC}"
echo "  Base URL:     ${BASE_URL}"
echo "  Models path:  /v1/models"
echo "  Auth:         NONE (local backend — no API key required)"
echo "  is_cloud:     False"
echo "  Default mode: OPENAI (primary endpoint /v1/chat/completions)"
echo "  Request:      GET ${BASE_URL}/v1/models"

HTTP_CODE=$(curl -s -m 5 -o /tmp/agentkthx_probe_llama_server.json -w "%{http_code}" \
    -H "User-Agent: AgentKthx-probe/0.x" \
    -H "Accept: application/json" \
    "${BASE_URL}/v1/models" 2>/dev/null) || HTTP_CODE="000"

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "  ${RED}✗ HTTP ${HTTP_CODE}${NC}"
    echo "  Response body (first 600 bytes):"
    if [ -s /tmp/agentkthx_probe_llama_server.json ]; then
        head -c 600 /tmp/agentkthx_probe_llama_server.json 2>/dev/null || true
        echo
    else
        echo "  (no response body — connection refused / timed out)"
    fi
    echo -e "  ${YELLOW}Hint:${NC} llama-server not running at ${BASE_URL}"
    echo "    Start it directly:  llama-server -m your-model.gguf --port 8764"
    echo "    Or set:             export LLAMA_SERVER_BASE_URL=http://host:port"
    echo ""
    echo "  Also try the TurboQuant path:"
    echo "    agentkthx turbo start qwen2.5:7b"
    exit 1
fi
echo -e "  ${GREEN}✓ HTTP 200${NC}  (raw JSON: /tmp/agentkthx_probe_llama_server.json)"

python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_llama_server.json')); assert isinstance(d.get('data'), list)" 2>/dev/null || {
    echo -e "  ${RED}✗ Response is not OpenAI-shaped {\"data\": [...]}.${NC}"
    head -c 600 /tmp/agentkthx_probe_llama_server.json
    echo
    exit 1
}

# Second curl — /slots (live slot state; NOT used by AgentKthx backend, new probe)
echo "  Request:      GET ${BASE_URL}/slots"
SLOTS_HTTP=$(curl -s -m 5 -o /tmp/agentkthx_probe_llama_server_slots.json -w "%{http_code}" \
    -H "User-Agent: AgentKthx-probe/0.x" \
    -H "Accept: application/json" \
    "${BASE_URL}/slots" 2>/dev/null || echo "000")
if [ "$SLOTS_HTTP" = "200" ]; then
    echo -e "  ${GREEN}✓ /slots HTTP 200${NC}  (raw JSON: /tmp/agentkthx_probe_llama_server_slots.json)"
else
    echo -e "  ${YELLOW}○ /slots HTTP ${SLOTS_HTTP} (slots state will be skipped in Section 6)${NC}"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Sections 2–6 — Python analysis (single heredoc, parses JSON + emits report)
# ═══════════════════════════════════════════════════════════════════════════
python3 << 'PYEOF'
import json
import os

CYAN = '\033[0;36m'; GREEN = '\033[0;32m'; YELLOW = '\033[0;33m'; RED = '\033[0;31m'; NC = '\033[0m'

d = json.load(open("/tmp/agentkthx_probe_llama_server.json"))
models = d.get("data", [])

# Load /slots if we got it
slots_path = "/tmp/agentkthx_probe_llama_server_slots.json"
slots = []
if os.path.exists(slots_path) and os.path.getsize(slots_path) > 0:
    try:
        slots = json.load(open(slots_path))
        if not isinstance(slots, list):
            slots = []
    except Exception:
        slots = []

# ───────────────────────────────────────────────────────────────────────────
# Section 2 — Static API surface
# (sourced from agentkthx/backends/llama_server.py — no
#  LLAMA_SERVER_API_TECHNICAL_REFERENCE.md exists in docs/api/)
# ───────────────────────────────────────────────────────────────────────────
REQUEST_PARAMS = [
    # (name, type, required, default, notes)
    ("model",              "string",            True,  None,    "Model id from /v1/models; for single-model llama-server, ignored server-side"),
    ("messages",           "array[Message]",    True,  None,    "Chat history; roles system/user/assistant/tool"),
    ("stream",             "bool",              False, "false", "SSE stream (server supports both)"),
    ("temperature",        "float",             False, "0.7",   "Always sent (backend hardcodes)"),
    ("max_tokens",         "int",               False, "2048",  "Always sent; mapped from num_predict"),
    ("top_p",              "float",             False, None,    "Nucleus sampling"),
    ("presence_penalty",   "float",             False, None,    "-2.0 to 2.0"),
    ("frequency_penalty",  "float",             False, None,    "-2.0 to 2.0"),
    ("stop",               "string|array",      False, None,    "Stop sequences (family-aware defaults added by backend)"),
    ("response_format",    "object",            False, None,    "{type: text|json_object|json_schema}"),
    ("tools",              "array[Tool]",       False, None,    "OpenAI function-calling schema"),
    ("tool_choice",        "string|object",     False, "auto",  "auto|none|required|{type:function,function:{name:X}}"),
    ("think",              "bool",              False, None,    "For qwen3/deepseek-r1 thinking models"),
    ("reasoning_effort",   "string",            False, None,    "For thinking models (silently ignored if unsupported)"),
    ("logprobs",           "bool",              False, None,    "Return logprobs"),
    ("top_logprobs",       "int",               False, None,    "Requires logprobs=true"),
    ("n",                  "int",               False, None,    "Number of completions"),
    ("user",               "string",            False, None,    "End-user id (no-op locally)"),
    # Server-accepted extras (not sent by Python client, but documented):
    # samplers (array), cache_prompt (bool), grammar (string GBNF), seed (int -1=random),
    # n_predict (int server-side cap), top_k (int), min_p (float), tfs_z, typical_p,
    # repeat_last_n, repeat_penalty, mirostat, mirostat_tau, mirostat_eta, penalize_nl,
    # ignore_eos, timings
]

RESPONSE_PARAMS = [
    # (name, type, notes)
    ("id",                       "string",            "Request id"),
    ("object",                   "string",            "'chat.completion'"),
    ("created",                  "int (unix ts)",     "Response timestamp"),
    ("model",                    "string",            "Model id used"),
    ("choices",                  "array[Choice]",     "One per n"),
    ("choices[].message",        "object",            "{role:'assistant', content, tool_calls?, prefix?}"),
    ("choices[].finish_reason",  "string",            "stop|length|tool_calls|content_filter (standard OpenAI); may emit 'model_length' for context overflow"),
    ("usage",                    "object",            "{prompt_tokens, completion_tokens, total_tokens} (may include timings object — server extension)"),
]

TOOL_CALLING = {
    "format": "OpenAI-style tools[] array (function-calling convention)",
    "tool_choice": "auto|none|required|{type:function,function:{name:X}}",
    "tool_call_id_format": "call_<alphanumeric> (OpenAI-style with 'call_' prefix)",
    "arguments_format": "JSON string",
    "parallel_tool_calls": "No field support",
    "message_shape": (
        '{"role":"assistant","content":"...",'
        '"tool_calls":[{"id":"call_xxx","type":"function",'
        '"function":{"name":"get_weather","arguments":"{\\"city\\":\\"NYC\\"}"}}]}'
    ),
    "tool_message": '{"role":"tool","content":"result","tool_call_id":"call_xxx","name":"get_weather"}',
    "server_may_reject": "HTTP 500 'unsupported param: tools' — backend catches and falls back to REACT",
    "live_test_ladder": (
        "test_tool_support() sends get_weather tool; "
        "NATIVE if tool_calls[] returned, "
        "REACT if content matches ReAct pattern, "
        "REACT if HTTP error"
    ),
    "doc_ref": "agentkthx/backends/llama_server.py (no LLAMA_SERVER_API_TECHNICAL_REFERENCE.md exists)",
}

print(f"\n{CYAN}── 2. Static API surface{NC}  (from agentkthx/backends/llama_server.py)")
print(f"  {CYAN}[Request params — POST /v1/chat/completions body]{NC}")
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
print(f"    format:               {TOOL_CALLING['format']}")
print(f"    tool_choice:          {TOOL_CALLING['tool_choice']}")
print(f"    tool_call_id_format:  {TOOL_CALLING['tool_call_id_format']}")
print(f"    arguments_format:     {TOOL_CALLING['arguments_format']}")
print(f"    parallel_tool_calls:  {TOOL_CALLING['parallel_tool_calls']}")
print(f"    message_shape:        {TOOL_CALLING['message_shape']}")
print(f"    tool_message:         {TOOL_CALLING['tool_message']}")
print(f"    server_may_reject:    {TOOL_CALLING['server_may_reject']}")
print(f"    live_test_ladder:     {TOOL_CALLING['live_test_ladder']}")
print(f"    doc_ref:              {TOOL_CALLING['doc_ref']}")

print(f"\n  {CYAN}[Server-accepted extras (documented but not sent by Python client)]{NC}")
print(f"    samplers, cache_prompt, grammar (GBNF), seed, n_predict, top_k,")
print(f"    min_p, tfs_z, typical_p, repeat_last_n, repeat_penalty,")
print(f"    mirostat, mirostat_tau, mirostat_eta, penalize_nl, ignore_eos, timings")

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
    if "meta" in sample and isinstance(sample["meta"], dict):
        print(f"  Sample meta keys:       {list(sample['meta'].keys())}")

# ───────────────────────────────────────────────────────────────────────────
# Section 4 — Card field availability
# (OpenAI-shape cards with llama-server `meta` extension)
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 4. Card field availability (live models) ──{NC}")
# Standard OpenAI fields:
STANDARD_FIELDS = ["id", "object", "created", "owned_by"]
# llama-server `meta` extension fields:
META_FIELDS = [
    "n_ctx", "n_ctx_train", "n_params", "size",
    "n_vocab", "n_embd", "ftype",
]
# Rich fields (present on cloud-hosted APIs like Mistral — NOT on llama-server):
RICH_FIELDS = [
    "max_context_length", "default_model_temperature", "pricing",
    "capabilities", "aliases", "deprecation",
]

def meta_field_getter(field):
    """Return count of models whose card (or card.meta) has this field."""
    n = 0
    for m in models:
        if field in m:
            n += 1
            continue
        meta = m.get("meta") or {}
        if isinstance(meta, dict) and field in meta and meta[field] is not None:
            n += 1
    return n

print(f"  {'Field':30s} {'Available':12s} {'Count':12s}")
print(f"  {'─'*30} {'─'*12} {'─'*12}")

print(f"  {CYAN}[Standard OpenAI fields]{NC}")
for field in STANDARD_FIELDS:
    n = sum(1 for m in models if field in m)
    if n == 0:
        status = f"{YELLOW}○ missing{NC}"
    elif n == len(models):
        status = f"{GREEN}✓ all{NC}"
    else:
        status = f"{YELLOW}~ partial{NC}"
    print(f"  {field:30s} {n}/{len(models):<10d}  {status}")

print(f"  {CYAN}[llama-server `meta` extension]{NC}")
for field in META_FIELDS:
    n = meta_field_getter(field)
    if n == 0:
        status = f"{YELLOW}○ missing{NC}"
    elif n == len(models):
        status = f"{GREEN}✓ all{NC}"
    else:
        status = f"{YELLOW}~ partial{NC}"
    print(f"  meta.{field:25s} {n}/{len(models):<10d}  {status}")

print(f"  {CYAN}[Rich fields (cloud-hosted only — expected absent)]{NC}")
for field in RICH_FIELDS:
    n = sum(1 for m in models if field in m)
    if n == 0:
        status = f"{YELLOW}○ missing{NC}"
    elif n == len(models):
        status = f"{GREEN}✓ all{NC}"
    else:
        status = f"{YELLOW}~ partial{NC}"
    print(f"  {field:30s} {n}/{len(models):<10d}  {status}")

# Availability summary (for Section 5)
def has_meta(field):
    return any((m.get("meta") or {}).get(field) is not None for m in models)

CTX_AVAILABLE = has_meta("n_ctx")
CTX_TRAIN_AVAILABLE = has_meta("n_ctx_train")
PARAMS_AVAILABLE = has_meta("n_params")
SIZE_AVAILABLE = has_meta("size")

print(f"\n  Availability summary (for per-model detail):")
print(f"    Runtime ctx (meta.n_ctx):        {'yes' if CTX_AVAILABLE else 'no'}  ({sum(1 for m in models if (m.get('meta') or {}).get('n_ctx') is not None)} models)")
print(f"    Trained ctx (meta.n_ctx_train):  {'yes' if CTX_TRAIN_AVAILABLE else 'no'}  ({sum(1 for m in models if (m.get('meta') or {}).get('n_ctx_train') is not None)} models)")
print(f"    Params (meta.n_params):          {'yes' if PARAMS_AVAILABLE else 'no'}  ({sum(1 for m in models if (m.get('meta') or {}).get('n_params') is not None)} models)")
print(f"    File size (meta.size):           {'yes' if SIZE_AVAILABLE else 'no'}  ({sum(1 for m in models if (m.get('meta') or {}).get('size') is not None)} models)")
print(f"    Pricing:                         N/A  (LOCAL backend — no pricing)")
print(f"    is_free / is_paid:               N/A  (LOCAL backend — no paid tier)")

# ───────────────────────────────────────────────────────────────────────────
# Section 5 — Per-model detail
# LOCAL backend: pricing="N/A", is_free="N/A", is_paid="N/A"
# No catalog — single-model server, every loaded model is usable.
# ───────────────────────────────────────────────────────────────────────────
def family_of(model_id: str) -> str:
    mid = model_id.lower()
    # Common GGUF family prefixes
    for fam_prefix in [
        ("qwen", "qwen"), ("llama", "llama"), ("mistral", "mistral"),
        ("mixtral", "mixtral"), ("phi", "phi"), ("gemma", "gemma"),
        ("deepseek", "deepseek"), ("yi", "yi"), ("phi-", "phi"),
        ("starcoder", "starcoder"), ("codellama", "codellama"),
        ("codegemma", "codegemma"), ("tinyllama", "tinyllama"),
        ("orca", "orca"), ("vicuna", "vicuna"), ("neural-chat", "neural-chat"),
        ("openchat", "openchat"), ("zephyr", "zephyr"),
    ]:
        if mid.startswith(fam_prefix[0]):
            return fam_prefix[1]
    # Fallback: token before first '-' or '.'
    sep = "-"
    if "." in mid and "-" not in mid:
        sep = "."
    return mid.split(sep)[0] if sep in mid else mid[:8]

def get_meta(m_card: dict, field: str) -> object:
    """Pull field from card.meta (preferred) or card top-level."""
    if field in m_card and m_card[field] is not None:
        return m_card[field]
    meta = m_card.get("meta") or {}
    if isinstance(meta, dict):
        return meta.get(field)
    return None

def fmt_ctx(m_card: dict) -> str:
    n_ctx = get_meta(m_card, "n_ctx")
    n_ctx_train = get_meta(m_card, "n_ctx_train")
    if n_ctx is not None and n_ctx_train is not None:
        return f"{int(n_ctx)}/{int(n_ctx_train)}"
    if n_ctx is not None:
        return f"{int(n_ctx)}/—"
    if n_ctx_train is not None:
        return f"—/{int(n_ctx_train)}"
    return "—"

def fmt_max_tok(m_card: dict) -> str:
    # llama-server does not expose max_output_tokens via /v1/models
    return "—"

def fmt_size(m_card: dict) -> str:
    size = get_meta(m_card, "size")
    if size is None:
        return "—"
    try:
        mb = float(size) / (1024.0 * 1024.0)
        if mb >= 1024:
            return f"{mb/1024:.2f} GB"
        return f"{mb:.1f} MB"
    except (ValueError, TypeError):
        return str(size)

def fmt_params(m_card: dict) -> str:
    p = get_meta(m_card, "n_params")
    if p is None:
        return "—"
    try:
        p = int(p)
        if p >= 1_000_000_000:
            return f"{p/1_000_000_000:.2f}B"
        if p >= 1_000_000:
            return f"{p/1_000_000:.1f}M"
        if p >= 1_000:
            return f"{p/1_000:.1f}K"
        return str(p)
    except (ValueError, TypeError):
        return str(p)

print(f"\n{CYAN}── 5. Per-model detail ──{NC}")
print(f"  (LOCAL backend — pricing/is_free/is_paid are N/A; no paid tier, no free tier)")
print(f"  Single-model server: at most 1 entry below. Models discovered live via /v1/models;")
print(f"  no static catalog. ctx column shows runtime/trained (meta.n_ctx / meta.n_ctx_train).")
print(f"")
print(f"  {'Model':40s} {'Family':14s} {'Ctx(run/train)':16s} {'MaxTok':8s} {'Size':12s} {'Params':10s} {'Pricing':10s} {'Free':6s} {'Paid':6s}")
print(f"  {'─'*40} {'─'*14} {'─'*16} {'─'*8} {'─'*12} {'─'*10} {'─'*10} {'─'*6} {'─'*6}")
for m in sorted(models, key=lambda x: x.get("id", "")):
    mid = m.get("id", "?")
    fam = family_of(mid)
    ctx = fmt_ctx(m)
    max_tok = fmt_max_tok(m)
    size = fmt_size(m)
    params = fmt_params(m)
    pricing = "N/A"
    free_s = "N/A"
    paid_s = "N/A"
    print(f"  {mid:40s} {fam:14s} {ctx:16s} {max_tok:8s} {size:12s} {params:10s} {pricing:10s} {free_s:6s} {paid_s:6s}")

if not models:
    print(f"  (no models loaded — llama-server may be running with --model-path but no GGUF active)")

# ───────────────────────────────────────────────────────────────────────────
# Section 6 — Loaded models vs available slots
# /slots is NOT used by AgentKthx backend — this is a net-new probe to surface
# inference-slot state on the running llama-server.
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 6. Loaded models vs available slots ──{NC}")
print(f"  Loaded models (from /v1/models):     {len(models)}")
for m in models:
    mid = m.get("id", "?")
    print(f"    {GREEN}✓{NC} {mid}")

print(f"  Active slots (from GET /slots):       {len(slots)}")
if slots:
    print(f"")
    print(f"  {'Slot id':10s} {'State':12s} {'n_past':12s} {'n_ctx':12s} {'Truncated':12s}")
    print(f"  {'─'*10} {'─'*12} {'─'*12} {'─'*12} {'─'*12}")
    for s in slots:
        sid = s.get("id", "?")
        is_proc = s.get("is_processing", False)
        state = f"{YELLOW}busy{NC}" if is_proc else f"{GREEN}idle{NC}"
        n_past = s.get("n_past", "—")
        n_ctx = s.get("n_ctx", "—")
        n_trunc = s.get("n_truncated", "—")
        print(f"  {str(sid):10s} {state:20s} {str(n_past):12s} {str(n_ctx):12s} {str(n_trunc):12s}")
    # Aggregate stats
    busy = sum(1 for s in slots if s.get("is_processing"))
    idle = len(slots) - busy
    print(f"\n  Aggregate: {busy} busy / {idle} idle / {len(slots)} total slots")
else:
    print(f"    (no /slots data — endpoint not reachable or returned empty list)")
    print(f"     /slots is NOT used by the AgentKthx backend; this is a net-new probe.")

# Reconciliation note
print(f"\n  {CYAN}Reconciliation:{NC}")
print(f"    /v1/models lists the LOADED model(s) — what the server will serve.")
print(f"    /slots lists the inference slots — concurrent decode contexts the server tracks.")
print(f"    For single-model llama-server: 1 loaded model, N slots (default N=1, set via --parallel).")
print(f"    Loaded model count and slot count need not match (slots scale with --parallel).")

print(f"\n{CYAN}{'─'*78}{NC}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_llama_server.json")
print(f"                  /tmp/agentkthx_probe_llama_server_slots.json")
print(f"{CYAN}{'─'*78}{NC}")
PYEOF
