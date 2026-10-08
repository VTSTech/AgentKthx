#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_siliconflow.sh — Validate SiliconFlow API Technical Reference (R07.29)
# ═══════════════════════════════════════════════════════════════════════════
# GET-only by default — ZERO billable usage (billing-table verified: GET
#              requests log no usage rows; only POST /chat/completions does).
# --deep flag: minimal POST /chat/completions per live model (max_tokens=5,
#              temperature=0.7, top_p=0.9 — non-default sampling to detect
#              fixed-param models). Free models cost ¥0; paid models burn a
#              fraction of a cent each. Use --filter to limit scope.
#              ⚡ BILLABLE USAGE — gated: requires --confirm-billable.
# --caps flag:  capability spot-check — plain chat + tools + enable_thinking
#              on/off (4 tiny POSTs per model). Default spot-check model is
#              the FREE Qwen/Qwen3-8B ($0.00, but still logged as billable
#              usage); add paid models via --cap-models "A,B,C". Settles the
#              CLI's "tools: native/react" and "think: yes/no" columns with
#              live evidence instead of doc-derived heuristics.
#              ⚡ BILLABLE USAGE — gated: requires --confirm-billable.
#
# SILICONFLOW_API_KEY REQUIRED — unlike NVIDIA, /v1/models is NOT open:
#              unauthenticated GET → 401 {"code":30014,"message":"Token is
#              invalid."} (verified live). Also auto-loaded from
#              ~/.agentkthx/.env if `agentkthx auth` was used (override:
#              AGENTKTHX_ENV_FILE). Keys: https://cloud.siliconflow.cn/account/ak
#
# Usage:  bash probe_siliconflow.sh                                  # GET-only (safe, $0)
#         bash probe_siliconflow.sh --deep                           # gated → opt-in hint
#         bash probe_siliconflow.sh --deep --confirm-billable        # + per-model inference
#         bash probe_siliconflow.sh --deep --confirm-billable --filter qwen3
#         bash probe_siliconflow.sh --caps --confirm-billable        # tools/thinking matrix
#         bash probe_siliconflow.sh --caps --confirm-billable --cap-models "Qwen/Qwen3-8B,deepseek-ai/DeepSeek-R1"
# Output: /tmp/agentkthx_probe_siliconflow.json          (raw /v1/models)
#         /tmp/agentkthx_probe_siliconflow_headers.txt   (response headers)
#         /tmp/agentkthx_probe_siliconflow_deep.json     (--deep results)
#         /tmp/agentkthx_probe_siliconflow_caps.json     (--caps results)
#
# Report sections:
#   1. Endpoint              — base URL, auth shape, key source, HTTP status
#   2. Live /v1/models       — total, ID format (Publisher/Model prefixes,
#                              bare IDs, charset sanity), provider
#                              distribution, raw sample cards
#   3. Card field avail.     — field-by-field availability on live cards —
#                              settles whether pricing / context_length /
#                              capabilities are API-exposed or must stay in
#                              the seed catalog (+ heuristics)
#   4. Endpoint surface      — /models/{id} detail, /user/info, /user/balance,
#                              pricing endpoint candidates, unauth behavior,
#                              rate-limit headers
#   5. Seed catalog drift    — 30-entry live-verified seed vs live (404
#                              candidates, enrichment gaps)
#   6. Non-chat filter       — _NON_CHAT_PATTERNS applied to live; names the
#                              media/audio families that currently LEAK
#                              through (CosyVoice, IndexTTS, fish-speech,
#                              Qwen-Image, Wan2.2, Z-Image)
#   7. Deep probe (--deep)   — per-model POST: status, time, reasoning flag,
#                              error class (404 / 400 / 429-balance vs TPM)
#   8. Capability matrix (--caps) — live tools + enable_thinking +
#                              reasoning_content verdicts vs doc claims
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ═══════════════════════════════════════════════════════════════════════════
# Load ~/.agentkthx/.env (same as `agentkthx` itself does at startup)
# ═══════════════════════════════════════════════════════════════════════════
# The `agentkthx auth` command persists API keys to ~/.agentkthx/.env
# (override with AGENTKTHX_ENV_FILE). Shell exports ALWAYS win — we only
# fill gaps, never clobber already-set variables.
ENV_FILE="${AGENTKTHX_ENV_FILE:-$HOME/.agentkthx/.env}"
SHELL_HAD_KEY=false
[ -n "${SILICONFLOW_API_KEY:-}" ] && SHELL_HAD_KEY=true
if [ -f "$ENV_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%%#*}"  # strip inline comments
        line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        [ -z "$line" ] && continue
        key="${line%%=*}"
        val="${line#*=}"
        key="$(echo "$key" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        if [[ "${val:0:1}" == '"' && "${val: -1}" == '"' ]]; then
            val="${val:1:-1}"
        elif [[ "${val:0:1}" == "'" && "${val: -1}" == "'" ]]; then
            val="${val:1:-1}"
        fi
        if [ -z "${!key:-}" ]; then
            export "$key=$val"
        fi
    done < "$ENV_FILE"
fi
if [ -z "${SILICONFLOW_API_KEY:-}" ]; then
    KEY_SOURCE="unset"
elif $SHELL_HAD_KEY; then
    KEY_SOURCE="shell env"
else
    KEY_SOURCE="${ENV_FILE} (via agentkthx auth)"
fi

API_KEY="${SILICONFLOW_API_KEY:-}"
BASE_URL="${SILICONFLOW_BASE_URL:-https://api.siliconflow.com/v1}"
MODELS_JSON="/tmp/agentkthx_probe_siliconflow.json"
HEADERS_TXT="/tmp/agentkthx_probe_siliconflow_headers.txt"
DEEP_JSON="/tmp/agentkthx_probe_siliconflow_deep.json"
CAPS_JSON="/tmp/agentkthx_probe_siliconflow_caps.json"

DEEP=false
CAPS=false
BILLABLE=false
FILTER=""
# Default spot-check = the FREE model only ($0.00 — free of CHARGE, not of
# usage-log entries; the R07.29 live run's billing row: 0.563K tokens, $0.0000).
# Paid spot-checks require --cap-models + --confirm-billable.
CAP_MODELS="Qwen/Qwen3-8B"

for arg in "$@"; do
    case "$arg" in
        --deep) DEEP=true ;;
        --caps) CAPS=true ;;
        --confirm-billable) BILLABLE=true ;;
        --filter) EXPECT_FILTER=true ;;
        --filter=*) FILTER="${arg#--filter=}" ;;
        --cap-models) EXPECT_CAPS=true ;;
        --cap-models=*) CAP_MODELS="${arg#--cap-models=}" ;;
        -h|--help) head -50 "$0" | tail -n +2; exit 0 ;;
        *)
            if [ "${EXPECT_FILTER:-false}" = "true" ]; then
                FILTER="$arg"; EXPECT_FILTER=false
            elif [ "${EXPECT_CAPS:-false}" = "true" ]; then
                CAP_MODELS="$arg"; EXPECT_CAPS=false
            else
                echo "WARN: unknown arg '$arg' (ignored)" >&2
            fi
            ;;
    esac
done

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; DIM='\033[2m'; NC='\033[0m'

# Find the repo root (parent of scripts/) — used by Sections 5–8
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SEED_FILE="$REPO_ROOT/agentkthx/data/model_seed.json"

# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Endpoint
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}${BOLD}═══ SiliconFlow API Probe ═══${NC}"
echo ""
echo -e "${CYAN}── 1. Endpoint ──${NC}"
echo "  Base URL:       ${BASE_URL}"
echo "  Models path:    GET /models  (auth required — 401 without Bearer)"
echo "  Chat path:      POST /chat/completions"
echo "  Region note:    api.siliconflow.com (intl) — same API also served at api.siliconflow.cn (CN)"
if [ -z "$API_KEY" ]; then
    echo -e "  Auth:           ${RED}✗ SILICONFLOW_API_KEY not set (checked shell env + ${ENV_FILE})${NC}"
    echo "                    export SILICONFLOW_API_KEY=sk-...    # or: agentkthx auth"
    exit 1
fi
echo "  Auth:           Bearer \$SILICONFLOW_API_KEY (source: ${KEY_SOURCE}, len=${#API_KEY}, prefix=${API_KEY:0:8}...)"
if $DEEP; then
    if $BILLABLE; then
        echo -e "  Deep probe:     ${YELLOW}ON — per-model POST (BILLABLE usage; free models ¥0, paid a fraction of a cent)${NC}"
    else
        echo -e "  Deep probe:     ${YELLOW}GATED — POSTs are billable; add --confirm-billable to run${NC}"
    fi
    [ -n "$FILTER" ] && echo "  Filter:         only testing models matching '${FILTER}'"
else
    echo "  Deep probe:     off (GET-only — zero billable usage)"
fi
if $CAPS; then
    if $BILLABLE; then
        echo -e "  Caps probe:     ${YELLOW}ON — 4 tiny POSTs per spot-check model (BILLABLE usage)${NC}"
    else
        echo -e "  Caps probe:     ${YELLOW}GATED — POSTs are billable; add --confirm-billable to run${NC}"
    fi
    echo "  Cap models:     ${CAP_MODELS}"
fi
echo "  Request:        GET ${BASE_URL}/models"

HTTP_CODE=$(curl -s -m 20 -D "$HEADERS_TXT" -o "$MODELS_JSON" -w "%{http_code}" \
    -H "Authorization: Bearer $API_KEY" \
    -H "User-Agent: AgentKthx-probe/0.x" \
    -H "Accept: application/json" \
    "${BASE_URL}/models" 2>/dev/null) || HTTP_CODE="000"
[ -z "$HTTP_CODE" ] && HTTP_CODE="000"

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "  ${RED}✗ HTTP ${HTTP_CODE}${NC}"
    if [ -s "$MODELS_JSON" ]; then
        echo "  Response body (first 600 bytes):"
        head -c 600 "$MODELS_JSON"
        echo
    else
        echo "  (empty response body — likely network failure, DNS issue, or timeout)"
    fi
    exit 1
fi
echo -e "  ${GREEN}✓ HTTP 200${NC}  (raw JSON: ${MODELS_JSON})"

# ═══════════════════════════════════════════════════════════════════════════
# Section 2 — Live /v1/models: catalog + ID format
# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}── 2. Live /v1/models (catalog + ID format) ──${NC}"

python3 - "$MODELS_JSON" << 'PYEOF'
import json, sys
from collections import Counter

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
d = json.load(open(sys.argv[1]))
if isinstance(d, list):
    models = d
elif isinstance(d.get("data"), list):
    models = d["data"]
elif isinstance(d.get("models"), list):
    models = d["models"]
else:
    models = []

ids = [m.get("id") for m in models if isinstance(m, dict) and m.get("id")]
print(f"  Total models:       {len(models)}")
print(f"  Top-level keys:     {list(d.keys()) if isinstance(d, dict) else '(top-level list)'}")

# ── ID format analysis ──
prefixed = [i for i in ids if "/" in i]
bare = [i for i in ids if "/" not in i]
print(f"\n  ID format:          {CYAN}<Publisher>/<Model-Name>{NC}  ({len(prefixed)}/{len(ids)} prefixed)")
if bare:
    print(f"  Bare IDs (no '/'):  {YELLOW}{len(bare)}{NC} — {', '.join(bare[:15])}")
    if len(bare) > 15:
        print(f"                        ... + {len(bare) - 15} more")
else:
    print(f"  Bare IDs (no '/'):  0")
print(f"  Case-sensitivity:   IDs contain uppercase ({sum(1 for i in ids if i != i.lower())}/{len(ids)}) — request bodies must match EXACTLY")
spaces = [i for i in ids if " " in i]
free_suffix = [i for i in ids if ":free" in i]
dupes = [k for k, v in Counter(ids).items() if v > 1]
print(f"  Charset sanity:     spaces={len(spaces)}  ':free' suffixes={len(free_suffix)} (OpenRouter-style — expect 0)  duplicates={len(dupes)}")

print(f"\n  Provider distribution (top 15 by prefix):")
providers = Counter(i.split("/")[0] if "/" in i else "(bare)" for i in ids)
for prov, count in providers.most_common(15):
    print(f"    {prov:28s} {count:4d}")

# ── Raw sample cards (the ground truth for Section 3) ──
by_id = {m.get("id"): m for m in models if isinstance(m, dict)}
samples = []
if models:
    samples.append(("first card", models[0]))
for want in ("Qwen/Qwen3-8B", "Kev-4B", "Qwen/Qwen-Image"):
    if want in by_id:
        samples.append((want, by_id[want]))
print(f"\n  Sample cards (full raw objects — what the API actually returns):")
for label, card in samples[:4]:
    print(f"  {CYAN}[{label}]{NC}")
    print("    " + json.dumps(card, indent=2, ensure_ascii=False).replace("\n", "\n    ")[:600])
PYEOF

# ═══════════════════════════════════════════════════════════════════════════
# Section 3 — Card field availability (pricing / capabilities verdict)
# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}── 3. Card field availability (pricing / capabilities) ──${NC}"

python3 - "$MODELS_JSON" << 'PYEOF'
import json, sys
from collections import Counter

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
d = json.load(open(sys.argv[1]))
if isinstance(d, list):
    models = d
elif isinstance(d.get("data"), list):
    models = d["data"]
elif isinstance(d.get("models"), list):
    models = d["models"]
else:
    models = []

total = len(models)
print(f"  Cards analyzed:     {total}")

# ── Dynamic union of every key observed on live cards ──
observed = Counter()
for m in models:
    if isinstance(m, dict):
        for k in m:
            observed[k] += 1
print(f"\n  All keys observed on live cards (union with counts):")
if observed:
    for k, n in sorted(observed.items(), key=lambda x: -x[1]):
        print(f"    {k:28s} {n}/{total}")
else:
    print(f"    (none — cards are not JSON objects?)")

# ── Fixed checklist: OpenAI-standard + rich metadata fields ──
STANDARD = ["id", "object", "created", "owned_by"]
RICH = ["pricing", "context_length", "max_context_length", "capabilities",
        "max_tokens", "max_output_tokens", "mode", "type", "sub_type",
        "tags", "description", "aliases", "name", "size", "deprecation",
        "input_price", "output_price"]
print(f"\n  Field checklist (standard OpenAI + rich metadata):")
print(f"  {'Field':28s} {'Available':12s}")
print(f"  {'─'*28} {'─'*12}")
for field in STANDARD + RICH:
    n = sum(1 for m in models if isinstance(m, dict) and field in m)
    if total == 0:
        status = f"{YELLOW}○ no cards{NC}"
    elif n == 0:
        status = f"{YELLOW}○ missing{NC}"
    elif n == total:
        status = f"{GREEN}✓ all{NC}"
    else:
        status = f"{YELLOW}~ partial{NC}"
    print(f"  {field:28s} {n}/{total:<10d}  {status}")

# ── Nested drill: sub-keys of any dict-valued field ──
nested = {}
for m in models:
    if isinstance(m, dict):
        for k, v in m.items():
            if isinstance(v, dict):
                nested.setdefault(k, Counter()).update(v.keys())
if nested:
    print(f"\n  Nested object fields (sub-key union):")
    for k, subs in nested.items():
        print(f"    {k}: {dict(subs)}")

# ── context_length value distribution (if present) ──
ctx = Counter(m.get("context_length") for m in models
              if isinstance(m, dict) and m.get("context_length") is not None)
if ctx:
    print(f"\n  context_length value distribution: {dict(ctx)}")

# ── Verdicts ──
pricing_fields = ("pricing", "input_price", "output_price")
ctx_fields = ("context_length", "max_context_length")
cap_fields = ("capabilities", "tags", "mode")
pricing_ok = any(any(f in m for f in pricing_fields) for m in models if isinstance(m, dict))
ctx_ok = any(any(f in m for f in ctx_fields) for m in models if isinstance(m, dict))
cap_ok = any(any(f in m for f in cap_fields) for m in models if isinstance(m, dict))

print(f"\n  {CYAN}VERDICTS (what this means for the plugin):{NC}")
print(f"    Pricing via /v1/models:       {'YES — parse it in list_models' if pricing_ok else 'NO'}")
if not pricing_ok:
    print(f"      → seed pricing (0/0 on the 2 verified free models) + https://cloud.siliconflow.cn/pricing")
    print(f"        remain the source of truth; no machine-readable pricing API (see Section 4)")
print(f"    Context length via /v1/models: {'YES — parse it in list_models' if ctx_ok else 'NO'}")
if not ctx_ok:
    print(f"      → CLI shows CloudBackend._DEFAULT_CONTEXT_FALLBACK=128000 ('125K') for most models")
    print(f"        and seed context_length=32768 ('32K') for the free models — NOT API data")
print(f"    Capabilities via /v1/models:  {'YES — parse it in list_models' if cap_ok else 'NO'}")
if not cap_ok:
    print(f"      → CLI tools/think columns are doc-derived heuristics (REACT patterns + seed)")
    print(f"        — run --caps for live per-model verdicts")
PYEOF

# ═══════════════════════════════════════════════════════════════════════════
# Section 4 — Endpoint surface (detail / account / pricing / unauth / headers)
# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}── 4. Endpoint surface ──${NC}"

sf_get() { # $1=label  $2=path  $3=outfile  $4=auth(true/false)
    local label="$1" path="$2" out="$3" use_auth="$4" code
    local args=(-s -m 15 -o "$out" -w "%{http_code}"
        -H "User-Agent: AgentKthx-probe/0.x" -H "Accept: application/json")
    if [ "$use_auth" = "true" ]; then
        args+=(-H "Authorization: Bearer $API_KEY")
    fi
    code=$(curl "${args[@]}" "${BASE_URL}${path}" 2>/dev/null) || code="000"
    echo "$code"
}

show_body() { # $1=outfile  $2=max-bytes
    if [ -s "$1" ]; then
        head -c "$2" "$1" | sed 's/^/    /'; echo
    else
        echo "    (empty body)"
    fi
}

PROBE_TMP="/tmp/agentkthx_probe_sf_surface.json"

echo "  Per-model detail endpoint (OpenAI-style GET /models/{id}):"
PROBE_D1="/tmp/agentkthx_probe_sf_detail1.json"
PROBE_D2="/tmp/agentkthx_probe_sf_detail2.json"
CODE_DETAIL=$(sf_get "detail" "/models/Qwen/Qwen3-8B" "$PROBE_D1" true)
echo "    GET /models/Qwen/Qwen3-8B (raw slash)     → HTTP ${CODE_DETAIL}"
CODE_DETAIL_ENC=$(sf_get "detail-enc" "/models/Qwen%2FQwen3-8B" "$PROBE_D2" true)
echo "    GET /models/Qwen%2FQwen3-8B (encoded)     → HTTP ${CODE_DETAIL_ENC}"
CODE_DETAIL_FAKE=$(sf_get "detail-fake" "/models/ThisModelDoesNotExist-xyz" "$PROBE_TMP" true)
echo "    GET /models/ThisModelDoesNotExist-xyz     → HTTP ${CODE_DETAIL_FAKE}  (baseline)"
if [ "$CODE_DETAIL" = "200" ]; then
    echo -e "    ${GREEN}✓ per-model detail endpoint EXISTS — body (raw slash form):${NC}"
    show_body "$PROBE_D1" 400
elif [ "$CODE_DETAIL_ENC" = "200" ]; then
    echo -e "    ${GREEN}✓ per-model detail endpoint EXISTS — body (percent-encoded form):${NC}"
    show_body "$PROBE_D2" 400
else
    echo -e "    ${YELLOW}○ no per-model detail endpoint (real + fake both non-200) — catalog = flat /models list only${NC}"
fi

echo ""
echo "  Account endpoints (SiliconFlow-native, not OpenAI-spec — balance feeds the"
echo "  plugin's 429 balance-vs-TPM classifier story):"
CODE_USERINFO=$(sf_get "userinfo" "/user/info" "$PROBE_TMP" true)
echo "    GET /user/info                             → HTTP ${CODE_USERINFO}"
if [ "$CODE_USERINFO" = "200" ]; then
    echo -e "    ${GREEN}✓ account endpoint exists:${NC}"
    show_body "$PROBE_TMP" 300
else
    echo -e "    ${YELLOW}○ not available — body:${NC}"
    show_body "$PROBE_TMP" 200
fi
CODE_USERBAL=$(sf_get "userbal" "/user/balance" "$PROBE_TMP" true)
echo "    GET /user/balance                          → HTTP ${CODE_USERBAL}"
if [ "$CODE_USERBAL" = "200" ]; then
    echo -e "    ${GREEN}✓ balance endpoint exists:${NC}"
    show_body "$PROBE_TMP" 300
else
    echo -e "    ${YELLOW}○ not available — body:${NC}"
    show_body "$PROBE_TMP" 200
fi

echo ""
echo "  Pricing endpoint candidates (expected 404 — pricing lives on the web page):"
CODE_PRICING=$(sf_get "pricing" "/pricing" "$PROBE_TMP" true)
echo "    GET /pricing                               → HTTP ${CODE_PRICING}"
CODE_MODPRICING=$(sf_get "modpricing" "/models/pricing" "$PROBE_TMP" true)
echo "    GET /models/pricing                        → HTTP ${CODE_MODPRICING}"
if [ "$CODE_PRICING" = "200" ] || [ "$CODE_MODPRICING" = "200" ]; then
    echo -e "    ${GREEN}✓ machine-readable pricing FOUND — body:${NC}"
    show_body "$PROBE_TMP" 400
else
    echo -e "    ${YELLOW}○ no pricing API — hardcode from https://cloud.siliconflow.cn/pricing (seed catalog)${NC}"
fi

echo ""
echo "  Unauthenticated /models (NVIDIA's is open — SiliconFlow's is not):"
CODE_UNAUTH=$(sf_get "unauth" "/models" "$PROBE_TMP" false)
echo "    GET /models (no Bearer)                    → HTTP ${CODE_UNAUTH}"
if [ "$CODE_UNAUTH" = "200" ]; then
    echo -e "    ${YELLOW}⚠ catalog is OPEN without auth (differs from the verified 401){NC}"
else
    echo -e "    ${GREEN}✓ auth required${NC} (verified live: 401 {\"code\":30014,\"message\":\"Token is invalid.\"})"
fi

echo ""
echo "  Rate-limit headers on /models:"
RL=$(grep -iE 'ratelimit|rate-limit|x-rate|x-request-id|server' "$HEADERS_TXT" 2>/dev/null | head -5 || true)
if [ -n "$RL" ]; then
    echo "$RL" | sed 's/^/    /'
else
    echo "    (no rate-limit headers observed)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Section 5 — Seed catalog drift
# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}── 5. Seed catalog drift ──${NC}"
echo "  Comparing AgentKthx's seed catalog (agentkthx/data/model_seed.json"
echo "  → 'siliconflow' key, chat-only, full <Author>/<Model> IDs) vs live /v1/models."

if [ ! -f "$SEED_FILE" ]; then
    echo -e "  ${RED}✗ Seed file not found: $SEED_FILE${NC}"
    echo "  (running from outside the repo checkout? Sections 5–6 degrade gracefully)"
else
    python3 - "$MODELS_JSON" "$SEED_FILE" << 'PYEOF'
import json, sys

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
d = json.load(open(sys.argv[1]))
if isinstance(d, list):
    models = d
elif isinstance(d.get("data"), list):
    models = d["data"]
elif isinstance(d.get("models"), list):
    models = d["models"]
else:
    models = []
live_ids = {m.get("id") for m in models if isinstance(m, dict) and m.get("id")}

seed = json.load(open(sys.argv[2]))
sf_seed = seed.get("siliconflow", {})
seed_keys = set(sf_seed.keys())

print(f"  Seed catalog:       {len(seed_keys)} chat-only entries")
print(f"  Live endpoint:      {len(live_ids)} models")

seed_on_live = seed_keys & live_ids
seed_not_live = seed_keys - live_ids
live_not_seed = live_ids - seed_keys

print(f"\n  Seed entries found on live endpoint:      {len(seed_on_live)}/{len(seed_keys)}")
print(f"  Seed entries NOT on live (will 404):      {len(seed_not_live)}")
for name in sorted(seed_not_live)[:20]:
    print(f"    {RED}-{name}{NC}")
if len(seed_not_live) > 20:
    print(f"    ... + {len(seed_not_live) - 20} more")
if not seed_not_live:
    print(f"    {GREEN}(none — catalog is current){NC}")

print(f"\n  Live models not in seed:                  {len(live_not_seed)}")
print(f"    (enrichment gaps — no context/pricing/tools metadata in the catalog;")
print(f"     includes non-chat models — Section 6 splits those out)")
for name in sorted(live_not_seed)[:15]:
    print(f"    {YELLOW}+{name}{NC}")
if len(live_not_seed) > 15:
    print(f"    ... + {len(live_not_seed) - 15} more (full list: raw JSON + Section 6)")

free = sorted(k for k, v in sf_seed.items()
              if isinstance(v.get("pricing"), dict)
              and v["pricing"].get("input") == 0 and v["pricing"].get("output") == 0)
print(f"\n  Free models (seed pricing 0/0):           {len(free)}")
for k in free:
    print(f"    {GREEN}✓{NC} {k}  (context_length={sf_seed[k].get('context_length', '—')})")
print(f"    (matches _is_free_model() — pricing-derived, no ':free' suffix convention)")
PYEOF
fi

# ═══════════════════════════════════════════════════════════════════════════
# Section 6 — Non-chat filter (blocklist) applied to live
# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}── 6. Non-chat filter (blocklist) ──${NC}"
echo "  The SiliconFlow plugin filters live models via _NON_CHAT_PATTERNS"
echo "  (ocr, -vl, embed, bge, rerank, mt, flux, stable-diffusion, tts, audio,"
echo "  whisper, omni, captioner, vision) — imported live from the plugin."

python3 - "$MODELS_JSON" "$REPO_ROOT" << 'PYEOF'
import json, os, re, sys

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
repo_root = sys.argv[2]
d = json.load(open(sys.argv[1]))
if isinstance(d, list):
    models = d
elif isinstance(d.get("data"), list):
    models = d["data"]
elif isinstance(d.get("models"), list):
    models = d["models"]
else:
    models = []
ids = [m.get("id") for m in models if isinstance(m, dict) and m.get("id")]

is_non_chat = None
try:
    os.environ.pop("SILICONFLOW_API_KEY", None)
    sys.path.insert(0, repo_root)
    from agentkthx.plugins.siliconflow.siliconflow import _is_non_chat_model
    is_non_chat = _is_non_chat_model
    print(f"  Imported _is_non_chat_model from agentkthx.plugins.siliconflow.siliconflow ✓")
except Exception as e:
    print(f"  {YELLOW}⚠ could not import plugin blocklist ({type(e).__name__}: {e}){NC}")
    print(f"    — running without filter (all {len(ids)} live models treated as kept)")

blocked = [i for i in ids if is_non_chat and is_non_chat(i)] if is_non_chat else []
kept = [i for i in ids if not (is_non_chat and is_non_chat(i))]

print(f"\n  Kept (chat-capable per blocklist):    {len(kept)} models")
print(f"  Blocked (non-chat):                   {len(blocked)} models")
for name in sorted(blocked)[:15]:
    print(f"    {YELLOW}-{name}{NC}")
if len(blocked) > 15:
    print(f"    ... + {len(blocked) - 15} more")
if not blocked:
    print(f"    (nothing matched — current live catalog has no OCR/VL/embed/rerank entries)")

# ── Leak suspects: media/audio families the blocklist does NOT cover ──
# The current 2026 catalog serves image/video/audio GENERATION models whose
# names don't match any _NON_CHAT_PATTERNS entry — they show up in
# `agentkthx models --backend sf` and in `--deep` POSTs (where they 400).
LEAK_PATTERNS = {
    "image generation":  r"image|seedream|flux",
    "video generation":  r"\bi2v\b|\bt2v\b|wan2\.|-video",
    "audio/tts":         r"cosyvoice|fish-speech|indextts|\btts-|-speech\b|voice",
}
leaks = []
for i in kept:
    for cat, pat in LEAK_PATTERNS.items():
        if re.search(pat, i.lower()):
            leaks.append((i, cat))
            break

print(f"\n  {YELLOW}⚠ Media/audio LEAK suspects — kept by the blocklist but not chat-text models:{NC}")
if leaks:
    for i, cat in sorted(leaks):
        print(f"    {YELLOW}~{NC} {i:48s} ({cat})")
    print(f"  Suggested additional _NON_CHAT_PATTERNS entries:")
    print(f"    r\"image\", r\"i2v\", r\"t2v\", r\"cosyvoice\", r\"fish-speech\",")
    print(f"    r\"indextts\", r\"-speech\", r\"voice\"")
    print(f"    (caveat: 'image' would also catch future vision-CHAT models —")
    print(f"     house blocklist is conservative by design, see module docstring)")
else:
    print(f"    (none — blocklist covers the current catalog)")

# ── True enrichment gaps: kept (chat) live models missing from the seed ──
seed_path = os.path.join(repo_root, "agentkthx", "data", "model_seed.json")
gaps = []
if os.path.isfile(seed_path):
    seed = json.load(open(seed_path))
    sf_seed = set(seed.get("siliconflow", {}).keys())
    gaps = sorted(i for i in kept if i not in sf_seed)
print(f"\n  Kept-but-unseeded (true catalog enrichment gaps):  {len(gaps)}")
for name in gaps[:20]:
    print(f"    {YELLOW}+{name}{NC}")
if len(gaps) > 20:
    print(f"    ... + {len(gaps) - 20} more")
if not gaps and os.path.isfile(seed_path):
    print(f"    {GREEN}(none — every chat-capable live model is already seeded){NC}")
PYEOF

# ═══════════════════════════════════════════════════════════════════════════
# Section 7 — Deep probe (optional): per-model inference test
# ═══════════════════════════════════════════════════════════════════════════
if ! $DEEP; then
    echo ""
    echo -e "${CYAN}── 7. Deep probe ──${NC}"
    echo -e "  ${DIM}(skipped — run with --deep to POST /chat/completions per kept model:${NC}"
    echo -e "   ${DIM}HTTP status, response time, reasoning flag, 429 balance-vs-TPM split;${NC}"
    echo -e "   ${DIM}free models ¥0, paid a fraction of a cent each; --filter limits scope)${NC}"
elif ! $BILLABLE; then
    echo ""
    echo -e "${CYAN}── 7. Deep probe ──${NC}"
    echo -e "  ${YELLOW}⚡ GATED — --deep POSTs /chat/completions once per kept model, which is${NC}"
    echo -e "  ${YELLOW}   BILLABLE USAGE (every POST logs a usage row, even $0.00 free-model${NC}"
    echo -e "  ${YELLOW}   calls). Re-run with:  --deep --confirm-billable${NC}"
    echo -e "  ${DIM}   (the GET requests above logged zero usage rows on the billing table)${NC}"
else
    echo ""
    echo -e "${CYAN}── 7. Deep probe (per-model inference test) ──${NC}"
    echo "  POST /chat/completions per kept model:"
    echo "    body: {model, messages:[{role:user, content:'Hi'}], max_tokens:5,"
    echo "           stream:false, temperature:0.7, top_p:0.9}"
    echo "    timeout: 30s per model"
    [ -n "$FILTER" ] && echo "  Filter: only testing models matching '${FILTER}'"

    export PROBE_SF_KEY="$API_KEY" PROBE_SF_BASE="$BASE_URL" \
        PROBE_SF_MODELS="$MODELS_JSON" PROBE_SF_REPO="$REPO_ROOT" \
        PROBE_SF_FILTER="$FILTER" PROBE_SF_DEEP_OUT="$DEEP_JSON"

    python3 << 'PYEOF'
import json, os, sys, time, urllib.request, urllib.error

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
API_KEY = os.environ["PROBE_SF_KEY"]
BASE_URL = os.environ["PROBE_SF_BASE"]
FILTER = os.environ["PROBE_SF_FILTER"]
OUT = os.environ["PROBE_SF_DEEP_OUT"]
REPO = os.environ["PROBE_SF_REPO"]

d = json.load(open(os.environ["PROBE_SF_MODELS"]))
if isinstance(d, list):
    models = d
elif isinstance(d.get("data"), list):
    models = d["data"]
elif isinstance(d.get("models"), list):
    models = d["models"]
else:
    models = []
ids = [m.get("id") for m in models if isinstance(m, dict) and m.get("id")]

is_non_chat = None
try:
    os.environ.pop("SILICONFLOW_API_KEY", None)
    sys.path.insert(0, REPO)
    from agentkthx.plugins.siliconflow.siliconflow import _is_non_chat_model
    is_non_chat = _is_non_chat_model
except Exception:
    pass

kept = [i for i in ids if not (is_non_chat and is_non_chat(i))]
if FILTER:
    kept = [i for i in kept if FILTER.lower() in i.lower()]
kept.sort()

print(f"  Testing {len(kept)} models...\n")
print(f"  {'Model':<50} {'Status':<8} {'Time':>6}  Notes")
print(f"  {'-'*50} {'-'*8} {'-'*6}  {'-'*44}")

results = {"ok": [], "error": [], "not_chat": []}

def extract_error(body_text):
    """SiliconFlow errors: {"code":30014,"message":"..."} or OpenAI {"error":{"message":...}}."""
    try:
        err = json.loads(body_text)
        if isinstance(err, dict):
            if isinstance(err.get("error"), dict):
                return err["error"].get("message", body_text)
            for k in ("message", "detail", "title"):
                if err.get(k):
                    return str(err[k])
    except (json.JSONDecodeError, ValueError):
        pass
    return body_text

for model_id in kept:
    body = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 5,
        "stream": False,
        "temperature": 0.7,
        "top_p": 0.9,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
            "User-Agent": "AgentKthx-probe/0.x",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            elapsed = time.time() - t0
            data = json.loads(resp.read().decode("utf-8"))
            msg = (data.get("choices") or [{}])[0].get("message", {})
            content = (msg.get("content") or "")[:30]
            reasoning = "reasoning_content" in msg and bool(msg.get("reasoning_content"))
            note = repr(content) if content else ("(reasoning-only)" if reasoning else "(empty content)")
            if reasoning:
                note += " +reasoning✓"
            print(f"  {model_id:<50} {'✓ 200':<8} {elapsed:>5.1f}s  {note}")
            results["ok"].append(model_id)
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        body_text = e.read().decode("utf-8", errors="replace")[:400] if e.fp else ""
        err_msg = extract_error(body_text)
        low = err_msg.lower()
        if e.code == 429 and any(w in low for w in ("balance", "quota", "insufficient", "arrear")) \
                and "tpm" not in low and "rate limit" not in low:
            cls = "429-BALANCE"
        elif e.code == 429:
            cls = "429-TPM"
        elif e.code == 404:
            cls = "404"
        elif e.code == 400 and any(w in low for w in ("not support", "does not", "chat", "modality", "multimodal")):
            cls = "400-NOTCHAT"
        elif e.code == 400:
            cls = "400-PARAM"
        elif e.code == 504:
            cls = "504-TIMEOUT"
        else:
            cls = str(e.code)
        print(f"  {model_id:<50} {f'✗ {e.code}':<8} {elapsed:>5.1f}s  [{cls}] {err_msg[:50]}")
        if cls == "400-NOTCHAT":
            results["not_chat"].append((model_id, err_msg))
        else:
            results["error"].append({"model": model_id, "status": e.code, "class": cls, "message": err_msg})
    except urllib.error.URLError as e:
        elapsed = time.time() - t0
        print(f"  {model_id:<50} {'NET':<8} {elapsed:>5.1f}s  {e.reason}")
        results["error"].append({"model": model_id, "status": "NET", "class": "NET", "message": str(e.reason)})
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  {model_id:<50} {'ERR':<8} {elapsed:>5.1f}s  {type(e).__name__}: {e}")
        results["error"].append({"model": model_id, "status": "ERR", "class": "ERR", "message": str(e)})

print(f"\n  Summary:")
print(f"    ✓ 200 OK:                    {len(results['ok'])}")
print(f"    ✗ errors:                    {len(results['error'])}")
print(f"    ~ 400 not-chat suspects:     {len(results['not_chat'])}  (blocklist leaks — see Section 6)")
for m, msg in results["not_chat"][:10]:
    print(f"        - {m}  ({msg[:60]})")
bal = [e for e in results["error"] if e.get("class") == "429-BALANCE"]
tpm = [e for e in results["error"] if e.get("class") == "429-TPM"]
if bal:
    print(f"    429 balance-exhausted:       {len(bal)}  (matches _looks_like_quota_exhaustion")
    print(f"        wording — top up at cloud.siliconflow.cn or switch to the free models)")
if tpm:
    print(f"    429 TPM (transient):         {len(tpm)}  (retryable — shared retry loop backs off)")

with open(OUT, "w") as f:
    json.dump({
        "ok": results["ok"],
        "error": results["error"],
        "not_chat": [{"model": m, "message": msg} for m, msg in results["not_chat"]],
    }, f, indent=2, ensure_ascii=False)
print(f"\n  Detailed results: {OUT}")
PYEOF
fi

# ═══════════════════════════════════════════════════════════════════════════
# Section 8 — Capability matrix (optional): tools + enable_thinking spot-check
# ═══════════════════════════════════════════════════════════════════════════
if ! $CAPS; then
    echo ""
    echo -e "${CYAN}── 8. Capability matrix ──${NC}"
    echo -e "  ${DIM}(skipped — run with --caps to live-test tools + enable_thinking on${NC}"
    echo -e "   ${DIM}the free default spot-check model (paid via --cap-models); settles${NC}"
    echo -e "   ${DIM}the CLI's 'tools: native/react' and 'think: yes/no' columns)${NC}"
elif ! $BILLABLE; then
    echo ""
    echo -e "${CYAN}── 8. Capability matrix ──${NC}"
    echo -e "  ${YELLOW}⚡ GATED — --caps POSTs /chat/completions 4x per spot-check model,${NC}"
    echo -e "  ${YELLOW}   which is BILLABLE USAGE (the free default Qwen/Qwen3-8B bills${NC}"
    echo -e "  ${YELLOW}   $0.00 but still logs a usage row; paid --cap-models cost real${NC}"
    echo -e "  ${YELLOW}   money). Re-run with:  --caps --confirm-billable${NC}"
else
    echo ""
    echo -e "${CYAN}── 8. Capability matrix (tools + thinking spot-check) ──${NC}"
    echo "  Doc claims being validated (docs/api/SILICONFLOW_API_TECHNICAL_REFERENCE.md):"
    echo "    - tools return 400 on reasoning models (R1, R1-Distill, Kimi-K2-Thinking) → ReAct"
    echo "      (GLM-Z1 is the doc contradiction — the plugin pins it NATIVE deliberately)"
    echo "    - enable_thinking: top-level bool, hybrid models (Qwen3, GLM-Z1);"
    echo "      default true for thinking-capable models"
    echo "    - doc-verified function callers: DeepSeek-V3, Qwen2.5-*-Instruct, GLM-4-32B/9B"
    echo ""

    export PROBE_SF_KEY="$API_KEY" PROBE_SF_BASE="$BASE_URL" \
        PROBE_SF_CAPS_MODELS="$CAP_MODELS" PROBE_SF_CAPS_OUT="$CAPS_JSON"

    python3 << 'PYEOF'
import json, os, time, urllib.request, urllib.error

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
API_KEY = os.environ["PROBE_SF_KEY"]
BASE_URL = os.environ["PROBE_SF_BASE"]
OUT = os.environ["PROBE_SF_CAPS_OUT"]
CAP_MODELS = [m.strip() for m in os.environ["PROBE_SF_CAPS_MODELS"].split(",") if m.strip()]

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name"}},
            "required": ["city"],
        },
    },
}

def post(body, timeout=60):
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
            "User-Agent": "AgentKthx-probe/0.x",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return "200", json.loads(resp.read().decode("utf-8")), time.time() - t0
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")[:300] if e.fp else ""
        msg = text
        try:
            err = json.loads(text)
            if isinstance(err, dict):
                msg = err.get("message") or (err.get("error") or {}).get("message") or text
        except (json.JSONDecodeError, ValueError):
            pass
        return str(e.code), {"_error": msg}, time.time() - t0
    except Exception as e:
        return "ERR", {"_error": f"{type(e).__name__}: {e}"}, time.time() - t0

def analyze(data):
    choices = data.get("choices") or [{}]
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    msg = choice.get("message") or {}
    if not isinstance(msg, dict):
        msg = {}
    tool_calls = bool(msg.get("tool_calls"))
    reasoning = bool(msg.get("reasoning_content"))
    content = (msg.get("content") or "").strip()
    finish = choice.get("finish_reason", "—")
    return {"tool_calls": tool_calls, "reasoning": reasoning,
            "content": content, "finish": finish}

print(f"  Spot-check models: {CAP_MODELS}")
print(f"  4 probes per model: plain chat / tools(+enable_thinking:false) /")
print(f"  enable_thinking:true / enable_thinking:false\n")

matrix = {}

for model in CAP_MODELS:
    base = {"model": model, "stream": False, "temperature": 0.0}

    # (1) plain chat — default body, NO enable_thinking (observes the default mode)
    code_p, data_p, t_p = post({**base, "max_tokens": 16,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}]})
    a_p = analyze(data_p) if code_p == "200" else None

    # (2) tools — enable_thinking:false so hybrid thinkers don't burn the budget thinking
    code_t, data_t, t_t = post({**base, "max_tokens": 64,
        "enable_thinking": False,
        "tool_choice": "auto",
        "tools": [WEATHER_TOOL],
        "messages": [{"role": "user", "content":
            "What is the current weather in Toronto? Call the get_weather tool."}]})
    a_t = analyze(data_t) if code_t == "200" else None

    # (3) thinking ON
    code_on, data_on, t_on = post({**base, "max_tokens": 256,
        "enable_thinking": True,
        "messages": [{"role": "user", "content": "What is 2+2? Answer with just the number."}]})
    a_on = analyze(data_on) if code_on == "200" else None

    # (4) thinking OFF
    code_off, data_off, t_off = post({**base, "max_tokens": 64,
        "enable_thinking": False,
        "messages": [{"role": "user", "content": "What is 2+2? Answer with just the number."}]})
    a_off = analyze(data_off) if code_off == "200" else None

    chat_ok = code_p == "200" and (a_p is not None)
    if code_t == "200" and a_t and a_t["tool_calls"]:
        tools_verdict, tools_s = "native", f"{GREEN}native ✓{NC}"
    elif code_t == "400":
        tools_verdict, tools_s = "react", f"{YELLOW}react (400){NC}"
    elif code_t == "200":
        tools_verdict, tools_s = "accepted-nocall", f"{YELLOW}accepted, no call{NC}"
    else:
        tools_verdict, tools_s = f"err-{code_t}", f"{RED}✗ HTTP {code_t}{NC}"

    on_r = bool(a_on and a_on["reasoning"])
    off_r = bool(a_off and a_off["reasoning"])
    if a_on and a_off:
        if on_r and not off_r:
            think_s = f"{GREEN}toggle ✓{NC}"
        elif on_r and off_r:
            think_s = f"{YELLOW}always-on{NC}"
        elif not on_r and not off_r:
            think_s = "no reasoning"
        else:
            think_s = f"{YELLOW}inverted?!{NC}"
    else:
        think_s = f"{RED}✗ {code_on}/{code_off}{NC}"

    default_r = bool(a_p and a_p["reasoning"])

    print(f"  {CYAN}[{model}]{NC}")
    if code_p == "200":
        print(f"    plain chat:    HTTP 200  content={(a_p or {}).get('content', '')[:20]!r}"
              f"  finish={(a_p or {}).get('finish', '—')}"
              f"  default_reasoning={'yes' if default_r else 'no'}")
    else:
        # R07.29 live-run crash fix: a non-200 plain-chat previously died on
        # a_p['content'] (NoneType not subscriptable) — print the error body.
        print(f"    plain chat:    HTTP {code_p}  body: {data_p.get('_error', '')[:90]}")
    if code_t == "400":
        print(f"    tools probe:   HTTP 400  body: {data_t.get('_error', '')[:80]}")
    else:
        tc = (a_t or {}).get("tool_calls")
        print(f"    tools probe:   HTTP {code_t}  tool_calls={'yes' if tc else 'no'}"
              f"  finish={a_t['finish'] if a_t else '—'}")
    print(f"    thinking on:   HTTP {code_on}  reasoning_content={'yes' if on_r else 'no'}"
          f"  content={(a_on or {}).get('content', '')[:20]!r}")
    print(f"    thinking off:  HTTP {code_off}  reasoning_content={'yes' if off_r else 'no'}"
          f"  content={(a_off or {}).get('content', '')[:20]!r}")
    print(f"    VERDICT:       chat={'✓' if chat_ok else '✗'}  tools={tools_s}  thinking={think_s}\n")

    matrix[model] = {
        "chat_ok": chat_ok,
        "tools_verdict": tools_verdict,
        "thinking_on_reasoning": on_r,
        "thinking_off_reasoning": off_r,
        "default_reasoning": default_r,
        "plain": {"code": code_p, **(a_p or {})},
        "tools": {"code": code_t, **(a_t or {}), "error": None if code_t == "200" else data_t.get("_error")},
        "think_on": {"code": code_on, **(a_on or {})},
        "think_off": {"code": code_off, **(a_off or {})},
    }

with open(OUT, "w") as f:
    json.dump(matrix, f, indent=2, ensure_ascii=False)

print(f"  Matrix (for model_seed.json / REACT-pattern updates):")
print(f"  {'Model':44s} {'Chat':5s} {'Tools':16s} {'Thinking':12s} DefaultThink")
print(f"  {'-'*44} {'-'*5} {'-'*16} {'-'*12} {'-'*11}")
for model, r in matrix.items():
    print(f"  {model:44s} {'✓' if r['chat_ok'] else '✗':5s} {r['tools_verdict']:16s}"
          f" {'on/off' if r['thinking_on_reasoning'] != r['thinking_off_reasoning'] else 'static':12s}"
          f" {'yes' if r['default_reasoning'] else 'no'}")
print(f"\n  Detailed results: {OUT}")
print(f"\n  {CYAN}Feed these verdicts into the seed catalog — they replace the CLI's '? unknown'{NC}")
print(f"  {CYAN}tools/think column data and validate the doc claims (GLM-Z1 NATIVE pin, R1 400).{NC}")
PYEOF
fi

# ═══════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}${BOLD}═══ Summary ═══${NC}"
python3 - "$MODELS_JSON" "$SEED_FILE" << 'PYEOF'
import json, os, sys

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
d = json.load(open(sys.argv[1]))
if isinstance(d, list):
    models = d
elif isinstance(d.get("data"), list):
    models = d["data"]
elif isinstance(d.get("models"), list):
    models = d["models"]
else:
    models = []
ids = [m.get("id") for m in models if isinstance(m, dict) and m.get("id")]
prefixed = sum(1 for i in ids if "/" in i)
bare = [i for i in ids if "/" not in i]

pricing_ok = any("pricing" in m or "input_price" in m for m in models if isinstance(m, dict))
ctx_ok = any("context_length" in m or "max_context_length" in m for m in models if isinstance(m, dict))
cap_ok = any("capabilities" in m or "mode" in m or "tags" in m for m in models if isinstance(m, dict))

seed_path = sys.argv[2]
seed_n = "—"
if os.path.isfile(seed_path):
    seed = json.load(open(seed_path))
    seed_n = len(seed.get("siliconflow", {}))

print(f"  Catalog:          {len(ids)} live models ({prefixed} prefixed <Author>/<Model>,"
      f" {len(bare)} bare: {', '.join(bare[:5]) or 'none'})")
print(f"                    raw JSON: /tmp/agentkthx_probe_siliconflow.json  (seed: {seed_n} chat-only entries)")
print(f"  ID format:        {'Publisher/Model-Name' if prefixed > len(ids) * 0.9 else 'MIXED — check Section 2'}"
      f" — exact, case-sensitive, no ':free' suffixes")
print(f"  Pricing:          {'API-exposed ✓' if pricing_ok else 'NOT available via API'}"
      f" — cards carry no pricing fields; pricing endpoints 404 (Section 4)")
print(f"  Context length:   {'API-exposed ✓' if ctx_ok else 'NOT available via API'}"
      f" — CLI '125K' = _DEFAULT_CONTEXT_FALLBACK=128000, '32K' = seed context_length")
print(f"  Capabilities:     {'API-exposed ✓' if cap_ok else 'NOT available via API'}"
      f" — tools/think columns are heuristics; run --caps for live verdicts")
print(f"\n  {CYAN}Next steps:{NC}")
print(f"    --deep --confirm-billable   per-model 200/429/400 sweep (BILLABLE)")
print(f"    --caps --confirm-billable   tools + enable_thinking matrix (BILLABLE;)")
print(f"                                default cap model = free Qwen/Qwen3-8B,")
print(f"                                paid via --cap-models)")
print(f"    Section 6 leaks             add blocklist patterns for media/audio models")
print(f"    Section 6 gaps              seed the unseeded chat-capable live models")
PYEOF