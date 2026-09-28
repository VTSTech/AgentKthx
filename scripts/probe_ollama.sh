#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_ollama.sh — Validate Ollama Backend (expanded)
# ═══════════════════════════════════════════════════════════════════════════
# Local-only probe (no tokens, no billing — pricing/free/paid = N/A).
# Requires: Ollama running on localhost:11434 (or OLLAMA_BASE_URL set)
# Usage:    bash probe_ollama.sh
# Output:   /tmp/agentkthx_probe_ollama.json      (/api/tags — installed models)
#           /tmp/agentkthx_probe_ollama_ps.json   (/api/ps   — running models)
#
# Report sections:
#   1. Endpoint            — base URL, auth shape (none), HTTP status, raw JSON path
#   2. Static API surface  — request params, response shape, tool calling
#                            format (sourced from agentkthx/backends/ollama.py;
#                            no TECHNICAL_REFERENCE.md exists — hardcoded)
#   3. Live /api/tags      — top-level keys, total count, sample card
#   4. Card field avail.   — /api/tags fields + /api/show fields (per-model POST)
#   5. Per-model detail    — id, family, ctx (runtime), max_tok (model max),
#                            size (MB); pricing=N/A, is_free=N/A, is_paid=N/A
#   6. Available vs running — installed (/api/tags) vs warm (/api/ps) split
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Endpoint
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}${BOLD}═══ Ollama Backend Validation Probe ═══${NC}"
echo ""
echo -e "${CYAN}── 1. Endpoint ──${NC}"
echo "  Base URL:     ${BASE_URL}"
echo "  Tags path:    /api/tags  (list installed models — one call, all models)"
echo "  PS path:      /api/ps   (currently-loaded 'warm' models with VRAM)"
echo "  Auth:         NONE (local server — Content-Type: application/json only)"
echo "  is_cloud:     False (no rate limits, no billing)"
echo "  Request:      GET ${BASE_URL}/api/tags"

# Note: curl's -w "%{http_code}" already emits "000" on connection refused,
# so no `|| echo "000"` fallback is needed — `|| true` just disarms set -e.
HTTP_CODE=$(curl -s -m 5 -o /tmp/agentkthx_probe_ollama.json -w "%{http_code}" \
    "${BASE_URL}/api/tags" 2>/dev/null) || true

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "  ${YELLOW}⚠ HTTP ${HTTP_CODE} — Ollama not running at ${BASE_URL}${NC}"
    echo "  Start it: ollama serve"
    echo "  Or set OLLAMA_BASE_URL: export OLLAMA_BASE_URL=http://your-host:11434"
    exit 1
fi
echo -e "  ${GREEN}✓ HTTP 200${NC}  (raw JSON: /tmp/agentkthx_probe_ollama.json)"

# Second lightweight call: /api/ps (running/warm models with VRAM usage)
PS_CODE=$(curl -s -m 5 -o /tmp/agentkthx_probe_ollama_ps.json -w "%{http_code}" \
    "${BASE_URL}/api/ps" 2>/dev/null) || true
if [ "$PS_CODE" != "200" ]; then
    echo -e "  ${YELLOW}⚠ HTTP ${PS_CODE} on /api/ps (running-models probe will be skipped)${NC}"
    rm -f /tmp/agentkthx_probe_ollama_ps.json
else
    echo -e "  ${GREEN}✓ HTTP 200${NC}  (raw JSON: /tmp/agentkthx_probe_ollama_ps.json)"
fi

python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_ollama.json')); assert isinstance(d.get('models'), list)" 2>/dev/null || {
    echo -e "  ${RED}✗ Response is not Ollama-shaped {\"models\": [...]}.${NC}"
    head -c 600 /tmp/agentkthx_probe_ollama.json
    echo
    exit 1
}

# ═══════════════════════════════════════════════════════════════════════════
# Sections 2–6 — Python analysis (single heredoc, parses JSON + emits report)
# Per-model /api/show POST calls are made via urllib inside the heredoc.
# ═══════════════════════════════════════════════════════════════════════════
OLLAMA_BASE_URL="$BASE_URL" python3 << 'PYEOF'
import json
import os
import urllib.request
import urllib.error

CYAN = '\033[0;36m'; GREEN = '\033[0;32m'; YELLOW = '\033[0;33m'; RED = '\033[0;31m'; NC = '\033[0m'

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

d = json.load(open("/tmp/agentkthx_probe_ollama.json"))
models = d.get("models", [])

# Load /api/ps response (may not exist if second curl failed)
ps_data = None
try:
    ps_data = json.load(open("/tmp/agentkthx_probe_ollama_ps.json"))
except Exception:
    pass
running_models = ps_data.get("models", []) if ps_data else []

# ───────────────────────────────────────────────────────────────────────────
# Section 2 — Static API surface
# No docs/api/OLLAMA_API_TECHNICAL_REFERENCE.md exists; source of truth is
# agentkthx/backends/ollama.py. Hardcoded in the probe (slow-moving).
# ───────────────────────────────────────────────────────────────────────────
REQUEST_PARAMS = [
    # (name, type, required, default, notes)
    ("model",              "string",            True,  None,    "Model name from /api/tags"),
    ("messages",           "array[Message]",    True,  None,    "Roles: system/user/assistant/tool. Wire delta: tool_calls[].function.arguments is an OBJECT, not a JSON string"),
    ("stream",             "bool",              False, "false", "Ollama streams NDJSON (not OpenAI SSE) when true — one JSON object per line"),
    ("tools",              "array[Tool]",       False, None,    "OpenAI-shaped function schemas"),
    ("format",             "string | object",   False, None,    "OLLAMA-UNIQUE: 'json' for JSON mode, or full JSON-schema object for structured output. Replaces response_format"),
    ("keep_alive",         "string | int",      False, "1m",    "OLLAMA-UNIQUE: how long to keep model in memory. '5m', '1h', 0 (immediate unload), or nanoseconds"),
    ("options",            "object",            False, None,    "OLLAMA-UNIQUE wrapper: all sampling params live INSIDE this block"),
    ("options.temperature","float",             False, "0.8",   "Ollama default 0.8 (differs from OpenAI 0.7)"),
    ("options.num_predict","int",               False, "128",   "Wire delta: Ollama's name for max_tokens"),
    ("options.num_ctx",    "int",               False, "2048",  "Wire delta: Ollama's name for context window. DEFAULTS TO 2048 — very small!"),
    ("options.top_p",      "float",             False, "0.9",   "Nucleus sampling"),
    ("options.top_k",      "int",               False, "40",    "Wire delta: top-k sampling — present in Ollama, NOT in OpenAI Chat Completions"),
    ("options.stop",       "array[string]",     False, None,    "Stop sequences"),
    ("options.seed",       "int",               False, None,    "Reproducibility seed"),
    ("options.repeat_penalty","float",          False, "1.1",   "Wire delta: Ollama's name for frequency_penalty (different semantics)"),
    ("options.presence_penalty","float",        False, None,    ""),
    ("options.frequency_penalty","float",       False, None,    ""),
    ("options.num_thread", "int",               False, None,    "CPU thread count"),
    ("options.num_gpu",   "int",               False, None,    "GPU layer offload count"),
    ("options.mirostat",  "int",               False, None,    "Mirostat sampler (Ollama-unique): 0=off, 1=v1, 2=v2"),
    ("options.mirostat_eta","float",           False, None,    "Mirostat learning rate"),
    ("options.mirostat_tau","float",            False, None,    "Mirostat target entropy"),
    ("think",              "bool | null",       False, None,    "OLLAMA-UNIQUE (qwen3, deepseek-r1): null=auto, false=disable thinking. Top-level, NOT in options"),
    ("reasoning_effort",   "string",            False, None,    "For OpenAI o-series / GLM-5 thinking models (silently ignored by others)"),
]

RESPONSE_PARAMS = [
    # (name, type, notes)
    ("model",              "string",            "Model name"),
    ("created_at",         "string (ISO)",     "Timestamp"),
    ("message",            "object",            "{role:'assistant', content, tool_calls?, thinking?, images?}"),
    ("message.tool_calls[].function.arguments","object","Wire delta: OBJECT, not JSON string (OpenAI sends string)"),
    ("message.thinking",   "string",            "OLLAMA-UNIQUE: chain-of-thought for qwen3/deepseek-r1 (separate key from content)"),
    ("done",               "bool",              "True when generation complete"),
    ("done_reason",        "string",            "Wire delta: Ollama's name for finish_reason. Values: stop|length|load (load is OLLAMA-UNIQUE — model-loading status)"),
    ("total_duration",     "int (nanoseconds)","OLLAMA-UNIQUE: total processing time"),
    ("load_duration",      "int (nanoseconds)","OLLAMA-UNIQUE: time to load model"),
    ("prompt_eval_count",  "int",               "Wire delta: Ollama's name for usage.prompt_tokens"),
    ("prompt_eval_duration","int (nanoseconds)","OLLAMA-UNIQUE"),
    ("eval_count",         "int",               "Wire delta: Ollama's name for usage.completion_tokens"),
    ("eval_duration",      "int (nanoseconds)","OLLAMA-UNIQUE"),
]

TOOL_CALLING = {
    "format": "OpenAI-style tools[] array (function-calling convention)",
    "tool_choice": "auto|none|required|{type:function,function:{name:X}} (forwarded)",
    "tool_call_id_format": "Opaque string, NO 'call_' prefix (wire delta vs OpenAI)",
    "arguments_format": "JSON OBJECT (already parsed) — wire delta vs OpenAI's JSON string",
    "parallel_tool_calls": "No field — Ollama always emits multiple tool_calls[] if model chooses to",
    "message_shape": '{"role":"assistant","content":"...","tool_calls":[{"id":"...","type":"function","function":{"name":"get_weather","arguments":{"city":"NYC"}}}]}',
    "tool_message": '{"role":"tool","content":"result","tool_call_id":"...","name":"get_weather"}',
    "thinking_key": "message.thinking (separate from content; OpenAI-compat uses reasoning_content)",
    "support_detection": "HTTP 400 'does not support tools' or 'unsupported param: tools' -> model has NONE support, backend falls back to ReAct",
    "doc_ref": "agentkthx/backends/ollama.py (no OLLAMA_API_TECHNICAL_REFERENCE.md exists)",
}

print(f"\n{CYAN}── 2. Static API surface{NC}  (from agentkthx/backends/ollama.py — no TECHNICAL_REFERENCE.md exists)")
print(f"  {CYAN}[Request params — POST /api/chat body]{NC}")
print(f"    {'Name':36s} {'Type':22s} {'Req':4s} {'Default':14s} Notes")
print(f"    {'─'*36} {'─'*22} {'─'*4} {'─'*14} {'─'*40}")
for name, typ, req, default, notes in REQUEST_PARAMS:
    req_s = "yes" if req else "no"
    default_s = str(default) if default is not None else "—"
    print(f"    {name:36s} {typ:22s} {req_s:4s} {default_s:14s} {notes}")

print(f"\n  {CYAN}[Response shape — /api/chat completion object]{NC}")
print(f"    {'Field':40s} {'Type':20s} Notes")
print(f"    {'─'*40} {'─'*20} {'─'*40}")
for name, typ, notes in RESPONSE_PARAMS:
    print(f"    {name:40s} {typ:20s} {notes}")

print(f"\n  {CYAN}[Tool calling format]{NC}")
print(f"    format:             {TOOL_CALLING['format']}")
print(f"    tool_choice:        {TOOL_CALLING['tool_choice']}")
print(f"    tool_call.id:       {TOOL_CALLING['tool_call_id_format']}")
print(f"    arguments:          {TOOL_CALLING['arguments_format']}")
print(f"    parallel_tool_calls:{TOOL_CALLING['parallel_tool_calls']}")
print(f"    message_shape:      {TOOL_CALLING['message_shape']}")
print(f"    tool_message:       {TOOL_CALLING['tool_message']}")
print(f"    thinking_key:       {TOOL_CALLING['thinking_key']}")
print(f"    support_detection:  {TOOL_CALLING['support_detection']}")
print(f"    doc_ref:            {TOOL_CALLING['doc_ref']}")

# ───────────────────────────────────────────────────────────────────────────
# Pre-fetch /api/show for each model (per-model POST, cached to avoid re-fetch)
# ───────────────────────────────────────────────────────────────────────────
show_cache = {}

def fetch_show(model_name: str) -> dict:
    """POST /api/show {"name": model_name} -> rich model info."""
    if model_name in show_cache:
        return show_cache[model_name]
    body = json.dumps({"name": model_name}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/api/show",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
            show_cache[model_name] = data
            return data
    except Exception:
        show_cache[model_name] = {}
        return {}

for m in models:
    name = m.get("name")
    if name:
        fetch_show(name)

# ───────────────────────────────────────────────────────────────────────────
# Section 3 — Live /api/tags response
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 3. Live /api/tags response ──{NC}")
print(f"  Total models:           {len(models)}")
print(f"  Top-level keys:         {list(d.keys())}")
if models:
    sample = models[0]
    print(f"  Sample model object:    {sample.get('name', '?')}")
    print(f"  Sample card keys:       {list(sample.keys())}")

# ───────────────────────────────────────────────────────────────────────────
# Section 4 — Card field availability
# Two sub-blocks: tags-level (from /api/tags) + show-level (from /api/show)
# ───────────────────────────────────────────────────────────────────────────
TAGS_FIELDS = [
    "name", "modified_at", "size", "digest",
    "details.parent_model", "details.format", "details.family", "details.families",
    "details.parameter_size", "details.quantization_level",
]
SHOW_FIELDS = [
    "license", "modelfile", "parameters", "template", "system",
    "model_info", "capabilities", "details",
]

def get_nested(obj, path):
    """Resolve dotted path; returns (present, value)."""
    cur = obj
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return False, None
        cur = cur[p]
    return True, cur

print(f"\n{CYAN}── 4. Card field availability ──{NC}")

print(f"\n  {CYAN}[4a. /api/tags fields — one call, all models]{NC}")
print(f"    {'Field':36s} {'Available':12s} {'Count':12s}")
print(f"    {'─'*36} {'─'*12} {'─'*12}")
for field in TAGS_FIELDS:
    n = sum(1 for m in models if get_nested(m, field)[0])
    if len(models) == 0:
        status = "—"
    elif n == 0:
        status = f"{YELLOW}○ missing{NC}"
    elif n == len(models):
        status = f"{GREEN}✓ all{NC}"
    else:
        status = f"{YELLOW}~ partial{NC}"
    print(f"    {field:36s} {n}/{len(models):<10d}  {status}")

print(f"\n  {CYAN}[4b. /api/show fields — per-model POST]{NC}")
print(f"    {'Field':36s} {'Available':12s} {'Count':12s}")
print(f"    {'─'*36} {'─'*12} {'─'*12}")
for field in SHOW_FIELDS:
    n = sum(1 for m in models if m.get("name") and field in show_cache.get(m["name"], {}))
    if len(models) == 0:
        status = "—"
    elif n == 0:
        status = f"{YELLOW}○ missing{NC}"
    elif n == len(models):
        status = f"{GREEN}✓ all{NC}"
    else:
        status = f"{YELLOW}~ partial{NC}"
    print(f"    {field:36s} {n}/{len(models):<10d}  {status}")

print(f"\n  /api/show calls performed: {len(show_cache)} (cached)")

# ───────────────────────────────────────────────────────────────────────────
# Section 5 — Per-model detail
# Local backend — pricing/is_free/is_paid are N/A for every model.
# ctx  (runtime num_ctx): from /api/show parameters "num_ctx N" line, fallback 2048.
# max_tok (model max):    from /api/show model_info[*.context_length].
# size:                   from /api/tags size (bytes); display in MB.
# ───────────────────────────────────────────────────────────────────────────
def parse_runtime_ctx(show_data: dict) -> str:
    """Extract runtime num_ctx from /api/show 'parameters' multiline string."""
    params = show_data.get("parameters", "") or ""
    for line in params.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] == "num_ctx":
            try:
                return str(int(parts[1]))
            except ValueError:
                return parts[1]
    return "2048"  # footgun default

def parse_model_max_ctx(show_data: dict) -> str:
    """Extract model max context length from /api/show model_info dict."""
    model_info = show_data.get("model_info") or {}
    for key, val in model_info.items():
        if key.endswith(".context_length"):
            return str(val)
    return "—"

def parse_family(tags_card: dict) -> str:
    return tags_card.get("details", {}).get("family", "?")

def fmt_size(size_bytes) -> str:
    if not size_bytes:
        return "—"
    return f"{size_bytes / 1024 / 1024:.1f}MB"

print(f"\n{CYAN}── 5. Per-model detail ──{NC}")
print(f"  (Local backend — pricing/is_free/is_paid are N/A for every model)")
print(f"  (Ctx = runtime num_ctx from /api/show parameters; MaxTok = model max from model_info)")
print(f"  {YELLOW}⚠ FOOTGUN: num_ctx defaults to 2048 when not set in Modelfile — very small!{NC}")
print()
print(f"  {'Model':36s} {'Family':14s} {'Ctx':12s} {'MaxTok':12s} {'Size':10s} {'Pricing':10s} {'Free':6s} {'Paid':6s}")
print(f"  {'─'*36} {'─'*14} {'─'*12} {'─'*12} {'─'*10} {'─'*10} {'─'*6} {'─'*6}")
for m in sorted(models, key=lambda x: x.get("name", "")):
    name = m.get("name", "?")
    fam = parse_family(m)
    show_data = show_cache.get(name, {})
    ctx = parse_runtime_ctx(show_data)
    max_tok = parse_model_max_ctx(show_data)
    size = fmt_size(m.get("size", 0))
    print(f"  {name:36s} {fam:14s} {ctx:12s} {max_tok:12s} {size:10s} {'N/A':10s} {'N/A':6s} {'N/A':6s}")

# ───────────────────────────────────────────────────────────────────────────
# Section 6 — Available vs running models (NOT free/paid split — local backend)
# Installed total from /api/tags; running total from /api/ps.
# Delta: installed but not running = cold (would need to be loaded on next req).
# ───────────────────────────────────────────────────────────────────────────
installed_names = sorted({m.get("name", "") for m in models if m.get("name")})
running_names = sorted({m.get("name", "") for m in running_models if m.get("name")})
cold_names = sorted(set(installed_names) - set(running_names))

print(f"\n{CYAN}── 6. Available vs running models ──{NC}")
print(f"  Installed total (/api/tags):   {len(installed_names)}")
print(f"  Running total (/api/ps):       {len(running_names)}")
print(f"  Cold (installed not running):  {len(cold_names)}")
print()

if running_names:
    print(f"  Running (warm) models with VRAM + expiry:")
    for m in running_models:
        name = m.get("name", "?")
        size_vram = m.get("size_vram", 0) or 0
        size_vram_mb = size_vram / 1024 / 1024
        expires = m.get("expires_at", "?")
        print(f"    {GREEN}✓{NC} {name:36s}  VRAM {size_vram_mb:8.1f}MB  expires {expires}")
else:
    print(f"  Running (warm) models: (none — all models are cold; next request will trigger load)")

if cold_names:
    print(f"\n  Cold models (would need load on next request):")
    for name in cold_names[:20]:
        print(f"    · {name}")
    if len(cold_names) > 20:
        print(f"    ... + {len(cold_names) - 20} more")

print(f"\n{CYAN}{'─'*78}{NC}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_ollama.json       (/api/tags — installed)")
if ps_data is not None:
    print(f"  Raw JSON saved: /tmp/agentkthx_probe_ollama_ps.json  (/api/ps   — running)")
print(f"{CYAN}{'─'*78}{NC}")
PYEOF
