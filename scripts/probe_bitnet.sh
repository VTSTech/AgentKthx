#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_bitnet.sh — Validate BitNet Local Backend (expanded)
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls — local backend, no tokens burned).
# BitNet is a LOCAL llama.cpp fork running with bitnet_mode=True:
#   - Port 8765 (different from llama-server's 8764)
#   - PRIMARY model discovery endpoint is /props (NOT /v1/models —
#     BitNet typically does not expose /v1/models).
#   - Inference path is POST /completion (llama.cpp native — OPENRE/ReAct
#     mode), NOT OpenAI /v1/chat/completions.
# Requires: BitNet server running locally
#   (agentkthx turbo start bitnet-b1.58-2b-4t) or manual launch.
# Usage:    bash probe_bitnet.sh
# Output:   /tmp/agentkthx_probe_bitnet.json      (raw /props or /v1/models)
#           /tmp/agentkthx_probe_bitnet_slots.json (raw /slots, may fail)
#
# Report sections:
#   1. Endpoint           — base URL, env vars, primary endpoint, HTTP status,
#                           raw JSON path, /v1/models→/props fallback
#   2. Static API surface — /completion request body (OPENRE/ReAct shape — NOT
#                           OpenAI), response shape (llama.cpp native — flat
#                           fields, no choices[]), ReAct tool calling, BitNet
#                           quirks (hardcoded repeat_penalty=1.3, prompt
#                           budget, max exchanges, degraded tokenizer)
#   3. Live /props        — top-level keys, loaded model, build_info, chat
#                           template, tokens
#   4. Card field avail.  — /v1/models fields (may be entirely absent — falls
#                           back to /props); /props field availability matrix
#   5. Per-model detail   — single loaded model: id (from /props model_path
#                           basename), family="bitnet", ctx (from
#                           default_generation_settings.n_ctx if exposed),
#                           max_tok/size="—", pricing=N/A, is_free=N/A,
#                           is_paid=N/A
#   6. Loaded model vs server health — /props (single loaded model),
#                           /slots (may not be exposed), /health (liveness)
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

# BitNet-specific env resolution: BITNET_BASE_URL wins; fall back to
# BITNET_TUNNEL (e.g. for cloudflare-tunneled CI runners); fall back to
# localhost:8765 (BitNet's default port — distinct from llama-server's 8764).
BASE_URL="${BITNET_BASE_URL:-${BITNET_TUNNEL:-http://localhost:8765}}"

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Endpoint
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}${BOLD}═══ BitNet Local Backend Probe ═══${NC}"
echo ""
echo -e "${CYAN}── 1. Endpoint ──${NC}"
echo "  Base URL:        ${BASE_URL}"
echo "  Env var:         BITNET_BASE_URL  (fallback: BITNET_TUNNEL, then http://localhost:8765)"
echo "  Auth:            NONE (local backend)"
echo "  BackendType:     BITNET"
echo "  Default API mode: OPENRE  (primary endpoint: /props for discovery, /completion for inference)"
echo ""
echo "  Primary discovery:  GET ${BASE_URL}/props"
echo "  Fallback discovery: GET ${BASE_URL}/v1/models   (BitNet typically does NOT expose this)"
echo "  Liveness:           GET ${BASE_URL}/health"
echo "  Slots (best-effort): GET ${BASE_URL}/slots"
echo ""

# Try /props FIRST — BitNet's primary discovery endpoint.
PROPS_CODE=$(curl -s -m 5 -o /tmp/agentkthx_probe_bitnet.json -w "%{http_code}" \
    -H "User-Agent: AgentKthx-probe/0.x" \
    -H "Accept: application/json" \
    "${BASE_URL}/props" 2>/dev/null || true)
# Guard against curl being killed before -w fires (e.g. SIGPIPE) — clamp to "000".
[ -z "$PROPS_CODE" ] && PROPS_CODE="000"

MODELS_CODE="000"
if [ "$PROPS_CODE" = "200" ]; then
    DISCOVERY_SOURCE="/props"
    echo -e "  ${GREEN}✓ /props HTTP 200${NC}  (raw JSON: /tmp/agentkthx_probe_bitnet.json)"
    # /v1/models is secondary — try it but tolerate failure (BitNet typically
    # does not expose it). The result feeds Section 4's availability matrix.
    MODELS_CODE=$(curl -s -m 5 -o /tmp/agentkthx_probe_bitnet_v1models.json -w "%{http_code}" \
        -H "User-Agent: AgentKthx-probe/0.x" \
        "${BASE_URL}/v1/models" 2>/dev/null || true)
    [ -z "$MODELS_CODE" ] && MODELS_CODE="000"
    if [ "$MODELS_CODE" = "200" ]; then
        echo -e "  ${GREEN}✓ /v1/models HTTP 200${NC}  (raw JSON: /tmp/agentkthx_probe_bitnet_v1models.json)"
    else
        echo -e "  ${YELLOW}○ /v1/models HTTP ${MODELS_CODE}${NC}  (expected — BitNet typically does not expose /v1/models)"
    fi
else
    # /props failed — try /v1/models as a fallback. Some BitNet builds DO
    # expose /v1/models even when /props is broken, so give the probe a chance.
    MODELS_CODE=$(curl -s -m 5 -o /tmp/agentkthx_probe_bitnet.json -w "%{http_code}" \
        -H "User-Agent: AgentKthx-probe/0.x" \
        -H "Accept: application/json" \
        "${BASE_URL}/v1/models" 2>/dev/null || true)
    [ -z "$MODELS_CODE" ] && MODELS_CODE="000"
    if [ "$MODELS_CODE" = "200" ]; then
        DISCOVERY_SOURCE="/v1/models"
        echo -e "  ${YELLOW}○ /props HTTP ${PROPS_CODE} — fell back to /v1/models HTTP 200${NC}  (raw JSON: /tmp/agentkthx_probe_bitnet.json)"
    else
        echo -e "  ${RED}✗ HTTP ${PROPS_CODE} on /props and HTTP ${MODELS_CODE} on /v1/models${NC}"
        if [ -s /tmp/agentkthx_probe_bitnet.json ]; then
            echo "  Response body (first 600 bytes of /props attempt):"
            head -c 600 /tmp/agentkthx_probe_bitnet.json
            echo
        else
            echo "  (no response body — connection refused before any bytes were written)"
        fi
        echo ""
        echo "  BitNet server not running at ${BASE_URL}."
        echo "  Start it with:"
        echo "    agentkthx turbo start bitnet-b1.58-2b-4t"
        echo "  Or set BITNET_BASE_URL / BITNET_TUNNEL to point at a running instance."
        exit 1
    fi
fi

# Best-effort: /slots (llama.cpp fork endpoint — may or may not be exposed).
SLOTS_CODE=$(curl -s -m 5 -o /tmp/agentkthx_probe_bitnet_slots.json -w "%{http_code}" \
    -H "User-Agent: AgentKthx-probe/0.x" \
    "${BASE_URL}/slots" 2>/dev/null || true)
[ -z "$SLOTS_CODE" ] && SLOTS_CODE="000"

# Best-effort: /health (liveness check — should return "OK" or HTTP 200).
HEALTH_CODE=$(curl -s -m 5 -o /tmp/agentkthx_probe_bitnet_health.txt -w "%{http_code}" \
    -H "User-Agent: AgentKthx-probe/0.x" \
    "${BASE_URL}/health" 2>/dev/null || true)
[ -z "$HEALTH_CODE" ] && HEALTH_CODE="000"

# ═══════════════════════════════════════════════════════════════════════════
# Sections 2–6 — Python analysis (single heredoc, parses JSON + emits report)
# ═══════════════════════════════════════════════════════════════════════════
DISCOVERY_SOURCE_VAL="${DISCOVERY_SOURCE}" \
MODELS_CODE_VAL="${MODELS_CODE}" \
SLOTS_CODE_VAL="${SLOTS_CODE}" \
HEALTH_CODE_VAL="${HEALTH_CODE}" \
python3 << 'PYEOF'
import json
import os
import re

CYAN = '\033[0;36m'; GREEN = '\033[0;32m'; YELLOW = '\033[0;33m'; RED = '\033[0;31m'; NC = '\033[0m'

DISCOVERY_SOURCE = os.environ.get("DISCOVERY_SOURCE_VAL", "/props")
MODELS_CODE      = os.environ.get("MODELS_CODE_VAL", "000")
SLOTS_CODE       = os.environ.get("SLOTS_CODE_VAL", "000")
HEALTH_CODE      = os.environ.get("HEALTH_CODE_VAL", "000")

RAW_PATH = "/tmp/agentkthx_probe_bitnet.json"
V1MODELS_PATH = "/tmp/agentkthx_probe_bitnet_v1models.json"
SLOTS_PATH    = "/tmp/agentkthx_probe_bitnet_slots.json"
HEALTH_PATH   = "/tmp/agentkthx_probe_bitnet_health.txt"

with open(RAW_PATH) as f:
    d = json.load(f)

# ───────────────────────────────────────────────────────────────────────────
# Section 2 — Static API surface
# Sourced from agentkthx/backends/bitnet.py + agentkthx/backends/llama_server.py
# (no BITNET_API_TECHNICAL_REFERENCE.md exists — backend code is source of
# truth). BitNetBackend(LlamaServerBackend) is a thin wrapper that forces
# bitnet_mode=True, so it inherits all OpenAI Chat Completions plumbing but
# defaults to OPENRE mode (/completion endpoint — llama.cpp native shape).
# ───────────────────────────────────────────────────────────────────────────
REQUEST_PARAMS = [
    # (name, type, required, default, notes)
    ("prompt",         "string",         True,  None,    "Full ReAct-formatted prompt (no chat template server-side; backend builds it from messages)"),
    ("n_predict",      "int",            True,  "2048",  "Server-side output cap; -1 = unlimited"),
    ("temperature",    "float",          True,  "0.7",   ""),
    ("stop",           "array[string]",  True,  "[]",    "Backend adds ['\\nUser: ', '\\nAssistant:'] + family stop tokens"),
    ("stream",         "bool",           False, "false", "Streaming SSE-like mode (newline-delimited JSON chunks, NOT SSE)"),
    ("repeat_penalty", "float",          False, "1.3",   "BITNET-HARDCODED to 1.3 to prevent degenerate loops"),
]

RESPONSE_PARAMS = [
    # (name, type, notes)
    ("content",             "string",  "Generated text (NO choices[] array — flat field)"),
    ("stop",                "bool",   "True when generation complete (NO finish_reason field)"),
    ("generation_settings", "object",  "Server settings echo"),
    ("tokens_evaluated",    "int",     "Input token count (wire delta: Ollama/llama.cpp name for usage.prompt_tokens)"),
    ("tokens_predicted",    "int",     "Output token count (wire delta: name for usage.completion_tokens)"),
    ("prompt",              "string",  "Echo of input prompt"),
    ("truncated",           "bool",    "Whether prompt was truncated"),
    ("timings",             "object",  "predicted_ms, predicted_n, predicted_per_second, eval_ms, eval_n, eval_per_second (server extension)"),
]
# Note: NO choices[] array, NO finish_reason field, NO usage object, NO tool_calls field

TOOL_CALLING = {
    "format":             "ReAct text-based tool calls embedded in the prompt (NO native tool support)",
    "tools_array":         "NOT sent server-side — /completion body only has prompt, n_predict, temperature, stop, repeat_penalty",
    "tool_choice":         "N/A — no native tools",
    "react_format":        "Available tools:\n- get_weather: Get the current weather for a location\nUse ReAct format for tool calls:\nAction: tool_name\nAction Input: {\"param\": \"value\"}\nFinal Answer: <result>",
    "tool_call_parsing":   "AgentKthx's tool_parse.py regex-extracts Action/Action Input/Final Answer from response content",
    "support_level":       "Always REACT (bitnet_mode=True forces this unconditionally)",
    "doc_ref":             "agentkthx/backends/bitnet.py + agentkthx/backends/llama_server.py (no BITNET_API_TECHNICAL_REFERENCE.md exists)",
}

# BitNet-specific quirks (from BitNetBackend) — surfaced so consumers know
# the constraints they're coding against.
BITNET_QUIRKS = [
    ("repeat_penalty",         "1.3",         "HARDCODED — overrides caller value to prevent degenerate loops"),
    ("_BITNET_PROMPT_BUDGET",  "1024",        "chars — backend truncates conversation history to fit"),
    ("_BITNET_MAX_EXCHANGES",  "4",           "turns — backend caps conversation depth"),
    ("bitnet_mode",            "True",        "Forced by BitNetBackend wrapper — selects OPENRE+ReAct path"),
    ("Tokenizer",              "degraded",    "BitNet's tokenizer may differ from canonical; round-trip may not be exact"),
]

print(f"\n{CYAN}── 2. Static API surface{NC}  (from agentkthx/backends/bitnet.py + llama_server.py)")
print(f"  Inference endpoint: POST /completion  (OPENRE/ReAct mode — NOT OpenAI /v1/chat/completions)")
print(f"  Backend class:       BitNetBackend(LlamaServerBackend) — bitnet_mode=True forced")
print(f"")
print(f"  {CYAN}[Request params — POST /completion body (llama.cpp native, OPENRE shape)]{NC}")
print(f"    {'Name':20s} {'Type':20s} {'Req':4s} {'Default':14s} Notes")
print(f"    {'─'*20} {'─'*20} {'─'*4} {'─'*14} {'─'*44}")
for name, typ, req, default, notes in REQUEST_PARAMS:
    req_s = "yes" if req else "no"
    default_s = str(default) if default is not None else "—"
    print(f"    {name:20s} {typ:20s} {req_s:4s} {default_s:14s} {notes}")

print(f"\n  {CYAN}[Response shape — /completion response (llama.cpp native — NOT OpenAI)]{NC}")
print(f"    {'Field':24s} {'Type':18s} Notes")
print(f"    {'─'*24} {'─'*18} {'─'*44}")
for name, typ, notes in RESPONSE_PARAMS:
    print(f"    {name:24s} {typ:18s} {notes}")
print(f"    (No choices[] array, no finish_reason field, no usage object, no tool_calls field)")

print(f"\n  {CYAN}[Tool calling format (ReAct text — NO native tools)]{NC}")
print(f"    format:           {TOOL_CALLING['format']}")
print(f"    tools_array:      {TOOL_CALLING['tools_array']}")
print(f"    tool_choice:      {TOOL_CALLING['tool_choice']}")
print(f"    react_format:")
for line in TOOL_CALLING['react_format'].split("\n"):
    print(f"      {line}")
print(f"    tool_call_parsing:{TOOL_CALLING['tool_call_parsing']}")
print(f"    support_level:    {TOOL_CALLING['support_level']}")
print(f"    doc_ref:          {TOOL_CALLING['doc_ref']}")

print(f"\n  {CYAN}[BitNet-specific quirks]{NC}")
print(f"    {'Setting':26s} {'Value':14s} Notes")
print(f"    {'─'*26} {'─'*14} {'─'*44}")
for name, value, notes in BITNET_QUIRKS:
    print(f"    {name:26s} {value:14s} {notes}")

# ───────────────────────────────────────────────────────────────────────────
# Section 3 — Live /props response (BitNet's primary discovery endpoint)
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 3. Live discovery response (source: {DISCOVERY_SOURCE}) ──{NC}")
print(f"  Top-level keys:        {list(d.keys())}")

# /props returns model_path, default_generation_settings, chat_template,
# bos_token, eos_token, build_info — extract the headline model name.
model_path = d.get("model_path")
model_name = "?"
if model_path:
    # Basename of the .gguf path — e.g. /models/bitnet-b1.58-2b-4t.gguf -> bitnet-b1.58-2b-4t
    model_name = os.path.basename(model_path)
    # Strip common suffixes
    for ext in (".gguf", ".bin"):
        if model_name.endswith(ext):
            model_name = model_name[:-len(ext)]
print(f"  Loaded model path:     {model_path or '(not exposed)'}")
print(f"  Loaded model name:     {model_name}")
print(f"  /v1/models status:     HTTP {MODELS_CODE}  ({'exposed' if MODELS_CODE == '200' else 'NOT exposed — fall back to /props'})")

dgs = d.get("default_generation_settings") or {}
if dgs:
    print(f"  default_generation_settings keys: {list(dgs.keys())}")
else:
    print(f"  default_generation_settings:      (not exposed)")

build_info = d.get("build_info")
if build_info:
    # build_info may be a dict (version, compiler, etc.) or a string.
    if isinstance(build_info, dict):
        print(f"  build_info:            {build_info}")
    else:
        print(f"  build_info:            {str(build_info)[:120]}")

chat_template = d.get("chat_template")
print(f"  chat_template exposed: {'yes' if chat_template else 'no'}")
print(f"  bos_token:             {d.get('bos_token', '(not exposed)')}")
print(f"  eos_token:             {d.get('eos_token', '(not exposed)')}")

# ───────────────────────────────────────────────────────────────────────────
# Section 4 — Card field availability
# /v1/models typically returns minimal OpenAI-shape cards {id, object,
# created, owned_by} — but BitNet may not expose /v1/models at all. We
# check both endpoints and report which fields the loaded model exposes.
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 4. Card field availability (live /props + /v1/models) ──{NC}")

# /v1/models availability (OpenAI-shape card fields)
v1_models = []
if MODELS_CODE == "200" and os.path.exists(V1MODELS_PATH):
    try:
        with open(V1MODELS_PATH) as f:
            v1d = json.load(f)
        v1_models = v1d.get("data", []) if isinstance(v1d, dict) else []
    except Exception:
        v1_models = []

# OpenAI-shape card fields one would expect from a cloud-style /v1/models
OPENAI_FIELDS = ["id", "object", "created", "owned_by"]
print(f"  /v1/models endpoint: HTTP {MODELS_CODE}  ({'exposed' if MODELS_CODE == '200' else 'Endpoint not exposed — fall back to /props'})")
if v1_models:
    print(f"  /v1/models cards returned: {len(v1_models)}")
    print(f"  {'OpenAI card field':28s} {'Available':12s}")
    print(f"  {'─'*28} {'─'*12}")
    for field in OPENAI_FIELDS:
        n = sum(1 for m in v1_models if field in m)
        status = f"{GREEN}✓ all{NC}" if n == len(v1_models) else (f"{YELLOW}~ partial{NC}" if n else f"{YELLOW}○ missing{NC}")
        print(f"  {field:28s} {n}/{len(v1_models):<10d}  {status}")
else:
    print(f"  OpenAI card fields: not applicable (no /v1/models cards)")
    print(f"  — BitNet relies on /props as primary; /v1/models may be absent entirely.")

# /props field availability matrix (BitNet's actual source of truth)
PROPS_FIELDS = [
    "model_path", "default_generation_settings", "chat_template",
    "bos_token", "eos_token", "build_info",
]
print(f"\n  /props endpoint: HTTP {'200' if DISCOVERY_SOURCE == '/props' else '(via fallback)'}  ({'exposed' if DISCOVERY_SOURCE == '/props' else 'fallback path'})")
print(f"  {'/props field':32s} {'Available':12s}")
print(f"  {'─'*32} {'─'*12}")
for field in PROPS_FIELDS:
    present = field in d and d[field] not in (None, "", [], {})
    status = f"{GREEN}✓ present{NC}" if present else f"{YELLOW}○ missing{NC}"
    print(f"  {field:32s} {'yes' if present else 'no':12s}  {status}")

# ───────────────────────────────────────────────────────────────────────────
# Section 5 — Per-model detail
# LOCAL backend — pricing/free/paid are N/A (hardcoded). BitNet is a
# single-model server: at most one entry. The loaded model id comes from
# /props model_path basename (family is always "bitnet"). ctx is read from
# default_generation_settings.n_ctx if exposed; max_tok and size are NOT
# exposed by /props (single-model server — no need for a static catalog).
# ───────────────────────────────────────────────────────────────────────────
def get_ctx_from_props(props: dict) -> str:
    dgs = props.get("default_generation_settings") or {}
    n_ctx = dgs.get("n_ctx") or dgs.get("ctx_size")
    return str(n_ctx) if n_ctx else "—"

print(f"\n{CYAN}── 5. Per-model detail ──{NC}")
print(f"  (Local backend — pricing/free/paid are N/A; BitNet is single-model; no static catalog)")
print(f"  (Model id is extracted from /props model_path basename; family is always 'bitnet'.)")
print(f"")
print(f"  {'Model':40s} {'Family':18s} {'Ctx':12s} {'MaxTok':12s} {'Size':10s} {'Pricing':10s} {'Free':6s} {'Paid':6s}")
print(f"  {'─'*40} {'─'*18} {'─'*12} {'─'*12} {'─'*10} {'─'*10} {'─'*6} {'─'*6}")
mid = model_name
fam = "bitnet"
ctx = get_ctx_from_props(d)
max_tok = "—"
size   = "—"
pricing = "N/A"
free = "N/A"
paid = "N/A"
print(f"  {mid:40s} {fam:18s} {ctx:12s} {max_tok:12s} {size:10s} {pricing:10s} {free:6s} {paid:6s}")
print(f"  (Single-model server — only the loaded model is shown above.)")

# ───────────────────────────────────────────────────────────────────────────
# Section 6 — Loaded model vs server health
# For a cloud backend this section would compare the loaded model to the
# full catalog; BitNet is local + single-model, so this section instead
# reports: which model is loaded (from /props model_path), whether /slots
# is exposed (llama.cpp fork slot state), and /health liveness.
# ───────────────────────────────────────────────────────────────────────────
print(f"\n{CYAN}── 6. Loaded model vs server health ──{NC}")
print(f"  Loaded model:        {mid}")
print(f"  Loaded model path:   {model_path or '(not exposed)'}")
print(f"  Source:              /props model_path  (BitNet's primary discovery endpoint)")
print(f"")

# /slots — best-effort. BitNet is a llama.cpp fork, so /slots MAY be
# exposed; if absent, note it.
print(f"  /slots endpoint:     HTTP {SLOTS_CODE}  ({'exposed' if SLOTS_CODE == '200' else 'slots endpoint not exposed'})")
if SLOTS_CODE == "200" and os.path.exists(SLOTS_PATH):
    try:
        with open(SLOTS_PATH) as f:
            slots = json.load(f)
        if isinstance(slots, list) and slots:
            print(f"  Slot count:          {len(slots)}")
            for i, s in enumerate(slots[:4]):
                state = s.get("state", "?") if isinstance(s, dict) else "?"
                n_ctx_slot = s.get("n_ctx", "?") if isinstance(s, dict) else "?"
                print(f"    slot[{i}]: state={state} n_ctx={n_ctx_slot}")
            if len(slots) > 4:
                print(f"    ... + {len(slots) - 4} more slots")
        elif isinstance(slots, list) and not slots:
            print(f"  Slot list:           empty (no active slots)")
        else:
            print(f"  Slot payload:        {str(slots)[:120]}")
    except Exception as e:
        print(f"  Slot parse error:    {e}")
elif SLOTS_CODE == "200":
    # 200 but no JSON file (shouldn't happen) — read raw
    try:
        with open(SLOTS_PATH) as f:
            raw = f.read(120)
        print(f"  Slot payload (raw):  {raw}")
    except Exception:
        pass

# /health — liveness check. Should be HTTP 200 with body "OK" or "ok".
print(f"")
print(f"  /health endpoint:    HTTP {HEALTH_CODE}  ({'alive' if HEALTH_CODE == '200' else 'not responding'})")
if HEALTH_CODE == "200" and os.path.exists(HEALTH_PATH):
    try:
        with open(HEALTH_PATH) as f:
            health_body = f.read(60).strip()
        print(f"  /health body:        {health_body!r}")
    except Exception:
        pass

print(f"")
print(f"  Note: BitNet is a single-model local server — Section 6 is simpler")
print(f"  than cloud backends. There is no catalog drift, no free/paid split,")
print(f"  and no per-model pricing to compare. The /props model_path is the")
print(f"  source of truth for 'which model is loaded'. /slots and /health")
print(f"  are best-effort liveness signals.")

print(f"\n{CYAN}{'─'*78}{NC}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_bitnet.json  ({DISCOVERY_SOURCE})")
if MODELS_CODE == "200":
    print(f"                 /tmp/agentkthx_probe_bitnet_v1models.json  (/v1/models)")
if SLOTS_CODE == "200":
    print(f"                 /tmp/agentkthx_probe_bitnet_slots.json  (/slots)")
if HEALTH_CODE == "200":
    print(f"                 /tmp/agentkthx_probe_bitnet_health.txt  (/health)")
print(f"{CYAN}{'─'*78}{NC}")
PYEOF
