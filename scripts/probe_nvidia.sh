#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_nvidia.sh — Validate NVIDIA NIM API Technical Reference
# ═══════════════════════════════════════════════════════════════════════════
# GET-only by default (no inference calls, no credits burned).
# --deep flag: does a minimal POST /chat/completions per model to check
#              account access + parameter acceptance (burns ~1 credit per
#              model tested).
#
# NVIDIA_API_KEY REQUIRED — /v1/models is open (returns catalog without
#                            auth) but /chat/completions requires a valid
#                            nvapi- key.
#
# Usage:    bash probe_nvidia.sh              # GET-only (no credits burned)
#           bash probe_nvidia.sh --deep       # also test inference per model
#           bash probe_nvidia.sh --deep --filter kimi  # only test kimi models
# Output:   /tmp/agentkthx_probe_nvidia.json  (raw /v1/models response)
#
# Report sections:
#   1. Endpoint           — base URL, auth shape, HTTP status
#   2. Live /v1/models    — total count, sample card, provider distribution
#   3. Seed catalog drift — which seed entries exist on the live endpoint,
#                            which are missing (404 candidates), which live
#                            entries aren't in the seed
#   4. Non-chat filter    — which live models the blocklist filters out
#   5. Deep probe (--deep only) — per-model inference test: HTTP status,
#                            response time, error message, top_p acceptance
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ═══════════════════════════════════════════════════════════════════════════
# Load ~/.agentkthx/.env (same as `agentkthx` itself does at startup)
# ═══════════════════════════════════════════════════════════════════════════
# The `agentkthx auth` command persists API keys to ~/.agentkthx/.env
# (override with AGENTKTHX_ENV_FILE). This file is a simple KEY=VALUE
# format with # comments and optional single/double quotes. We load it
# here so the probe script can see keys that were set via `agentkthx auth`
# without requiring the user to also export them in their shell.
#
# Shell exports ALWAYS win (mirrors agentkthx.env_file.load_env_file) —
# we only fill gaps, never clobber already-set variables.
ENV_FILE="${AGENTKTHX_ENV_FILE:-$HOME/.agentkthx/.env}"
if [ -f "$ENV_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        # Skip comments and blank lines
        line="${line%%#*}"  # strip inline comments
        line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        [ -z "$line" ] && continue
        # Parse KEY=VALUE
        key="${line%%=*}"
        val="${line#*=}"
        key="$(echo "$key" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        # Strip optional surrounding quotes from value
        if [[ "${val:0:1}" == '"' && "${val: -1}" == '"' ]]; then
            val="${val:1:-1}"
        elif [[ "${val:0:1}" == "'" && "${val: -1}" == "'" ]]; then
            val="${val:1:-1}"
        fi
        # Only set if not already in the environment (shell exports win)
        if [ -z "${!key:-}" ]; then
            export "$key=$val"
        fi
    done < "$ENV_FILE"
fi

API_KEY="${NVIDIA_API_KEY:-}"
BASE_URL="${NVIDIA_BASE_URL:-https://integrate.api.nvidia.com/v1}"
DEEP=false
FILTER=""

# Parse args
for arg in "$@"; do
    case "$arg" in
        --deep) DEEP=true ;;
        --filter) shift_next=true ;;
        *)
            if [[ "${shift_next:-}" == "true" ]]; then
                FILTER="$arg"
                shift_next=false
            fi
            ;;
    esac
done

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; DIM='\033[2m'; NC='\033[0m'

# ═══════════════════════════════════════════════════════════════════════════
# Section 1 — Endpoint
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}${BOLD}═══ NVIDIA NIM API Probe ═══${NC}"
echo ""
echo -e "${CYAN}── 1. Endpoint ──${NC}"
echo "  Base URL:        ${BASE_URL}"
echo "  Models path:     /models  (open — returns catalog without auth)"
echo "  Chat path:       /chat/completions  (requires nvapi- key)"
if [ -n "$API_KEY" ]; then
    echo "  Auth:            Bearer \$NVIDIA_API_KEY (len=${#API_KEY}, prefix=${API_KEY:0:8}...)"
else
    echo -e "  Auth:            ${RED}NVIDIA_API_KEY unset — /v1/models works but --deep will fail${NC}"
fi
if $DEEP; then
    echo -e "  Deep probe:      ${YELLOW}ON (will burn ~1 credit per model tested)${NC}"
    [ -n "$FILTER" ] && echo "  Filter:          only testing models matching '${FILTER}'"
else
    echo "  Deep probe:      off (GET-only — no credits burned)"
fi
echo "  Request:         GET ${BASE_URL}/models"

# ═══════════════════════════════════════════════════════════════════════════
# Section 2 — Live /v1/models
# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}── 2. Live /v1/models ──${NC}"

CURL_HEADERS=(
    -H "User-Agent: AgentKthx-probe/0.x"
    -H "Accept: application/json"
)
[ -n "$API_KEY" ] && CURL_HEADERS+=(-H "Authorization: Bearer $API_KEY")

HTTP_CODE=$(curl -s -m 15 -o /tmp/agentkthx_probe_nvidia.json -w "%{http_code}" \
    "${CURL_HEADERS[@]}" \
    "${BASE_URL}/models" 2>/dev/null) || HTTP_CODE="000"
[ -z "$HTTP_CODE" ] && HTTP_CODE="000"

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "  ${RED}✗ HTTP ${HTTP_CODE}${NC}"
    if [ -s /tmp/agentkthx_probe_nvidia.json ]; then
        echo "  Response body (first 600 bytes):"
        head -c 600 /tmp/agentkthx_probe_nvidia.json
        echo
    else
        echo "  (empty response body — likely network failure, DNS issue, or timeout)"
    fi
    exit 1
fi

echo -e "  ${GREEN}✓ HTTP 200${NC}"
TOTAL=$(python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_nvidia.json')); print(len(d.get('data', [])))")
echo "  Total models:    ${TOTAL}"
echo "  Raw JSON:        /tmp/agentkthx_probe_nvidia.json"

# Provider distribution
echo ""
echo "  Provider distribution (top 10 by author prefix):"
python3 -c "
import json
from collections import Counter
d = json.load(open('/tmp/agentkthx_probe_nvidia.json'))
models = d.get('data', [])
providers = Counter()
for m in models:
    name = m.get('id', '')
    provider = name.split('/')[0] if '/' in name else '(no prefix)'
    providers[provider] += 1
for prov, count in providers.most_common(10):
    print(f'    {prov:30s} {count:4d}')
"

# Sample card
echo ""
echo "  Sample model card (first entry):"
python3 -c "
import json
d = json.load(open('/tmp/agentkthx_probe_nvidia.json'))
models = d.get('data', [])
if models:
    print(json.dumps(models[0], indent=2, ensure_ascii=False)[:500])
"

# ═══════════════════════════════════════════════════════════════════════════
# Section 3 — Seed catalog drift
# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}── 3. Seed catalog drift ──${NC}"
echo "  Comparing AgentKthx's seed catalog (agentkthx/data/model_seed.json"
echo "  → 'nvidia' key) against the live /v1/models endpoint."

# Find the repo root (parent of scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SEED_FILE="$REPO_ROOT/agentkthx/data/model_seed.json"

if [ ! -f "$SEED_FILE" ]; then
    echo -e "  ${RED}✗ Seed file not found: $SEED_FILE${NC}"
else
    python3 -c "
import json

# Load seed catalog
with open('$SEED_FILE') as f:
    seed = json.load(f)
nv_seed = seed.get('nvidia', {})
seed_keys = set(nv_seed.keys())
print(f'  Seed catalog:    {len(seed_keys)} NVIDIA entries')

# Load live models
with open('/tmp/agentkthx_probe_nvidia.json') as f:
    live = json.load(f)
live_ids = {m['id'] for m in live.get('data', []) if m.get('id')}
print(f'  Live endpoint:   {len(live_ids)} models')
print()

# R07.26 follow-up #3: the seed catalog now keys on FULL prefixed IDs
# (e.g. 'meta/llama-3.3-70b-instruct') matching what the API body expects.
# Compare directly against the live IDs (no prefix stripping needed).
seed_on_live = {s for s in seed_keys if s in live_ids}
seed_not_on_live = seed_keys - live_ids
live_not_in_seed = live_ids - seed_keys

print(f'  Seed entries found on live endpoint:     {len(seed_on_live)}/{len(seed_keys)}')
print(f'  Seed entries NOT on live endpoint (404):  {len(seed_not_on_live)}')
if seed_not_on_live:
    print(f'    ${RED}These seed entries will 404 — remove from catalog or fix ID:${NC}')
    for name in sorted(seed_not_on_live):
        print(f'      - {name}')

print()
print(f'  Live models not in seed (enrichment gaps): {len(live_not_in_seed)}')
print(f'    (these work but lack accurate context_length + pricing metadata)')
"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Section 4 — Non-chat filter (blocklist)
# ═══════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}── 4. Non-chat filter (blocklist) ──${NC}"
echo "  The NVIDIA plugin filters live models via _NON_CHAT_PATTERNS blocklist"
echo "  (embeddings, reward, safety, vision, translation, specialized tools)."

python3 -c "
import json, sys, os
sys.path.insert(0, '$REPO_ROOT')
# Avoid env-var requirement for the blocklist function import
os.environ.pop('NVIDIA_API_KEY', None)
from agentkthx.plugins.nvidia.nvidia import _is_non_chat_model

with open('/tmp/agentkthx_probe_nvidia.json') as f:
    live = json.load(f)
models = live.get('data', [])

blocked = [m['id'] for m in models if _is_non_chat_model(m.get('id', ''))]
kept = [m['id'] for m in models if not _is_non_chat_model(m.get('id', ''))]

print(f'  Kept (chat-capable):     {len(kept)} models')
print(f'  Blocked (non-chat):      {len(blocked)} models')
print()
if blocked:
    print('  Blocked models (sample, first 15):')
    for name in sorted(blocked)[:15]:
        print(f'    - {name}')
    if len(blocked) > 15:
        print(f'    ... and {len(blocked) - 15} more')
"

# ═══════════════════════════════════════════════════════════════════════════
# Section 5 — Deep probe (optional)
# ═══════════════════════════════════════════════════════════════════════════
if ! $DEEP; then
    echo ""
    echo -e "${CYAN}── 5. Deep probe ──${NC}"
    echo -e "  ${DIM}(skipped — run with --deep to test inference per model)${NC}"
    echo ""
    echo -e "${DIM}Deep probe does a minimal POST /chat/completions per kept model${NC}"
    echo -e "${DIM}to check: (a) account access, (b) top_p acceptance, (c) response time.${NC}"
    echo -e "${DIM}Burns ~1 credit per model tested. Use --filter to limit scope.${NC}"
    exit 0
fi

if [ -z "$API_KEY" ]; then
    echo -e "  ${RED}✗ --deep requires NVIDIA_API_KEY${NC}"
    exit 1
fi

echo ""
echo -e "${CYAN}── 5. Deep probe (inference test per model) ──${NC}"
echo "  Sending minimal POST /chat/completions per kept model:"
echo "    body: {model, messages:[{role:user, content:'Hi'}], max_tokens:5, stream:false}"
echo "    timeout: 30s per model"
[ -n "$FILTER" ] && echo "  Filter: only testing models matching '${FILTER}'"
echo ""

python3 -c "
import json, sys, os, time, urllib.request, urllib.error
sys.path.insert(0, '$REPO_ROOT')
os.environ.pop('NVIDIA_API_KEY', None)
from agentkthx.plugins.nvidia.nvidia import _is_non_chat_model

API_KEY = '$API_KEY'
BASE_URL = '$BASE_URL'
FILTER = '$FILTER'

with open('/tmp/agentkthx_probe_nvidia.json') as f:
    live = json.load(f)
models = live.get('data', [])

# Filter to chat-capable models
kept = [m['id'] for m in models if not _is_non_chat_model(m.get('id', ''))]
# Apply optional filter
if FILTER:
    kept = [m for m in kept if FILTER.lower() in m.lower()]

print(f'  Testing {len(kept)} models...')
print()
print(f'  {\"Model\":<55} {\"Status\":<8} {\"Time\":>6}  Notes')
print(f'  {\"-\"*55} {\"-\"*8} {\"-\"*6}  {\"-\"*40}')

results = {'ok': [], 'error': [], 'blocked': []}

for model_id in sorted(kept):
    body = json.dumps({
        'model': model_id,
        'messages': [{'role': 'user', 'content': 'Hi'}],
        'max_tokens': 5,
        'stream': False,
        'temperature': 0.7,
        'top_p': 0.9,  # intentionally non-default to detect fixed-param models
    }).encode('utf-8')
    req = urllib.request.Request(
        f'{BASE_URL}/chat/completions',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {API_KEY}',
        },
        method='POST',
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            elapsed = time.time() - t0
            data = json.loads(resp.read().decode('utf-8'))
            content = ''
            if data.get('choices'):
                content = data['choices'][0].get('message', {}).get('content', '')[:30]
            print(f'  {model_id:<55} {\"✓ 200\":<8} {elapsed:>5.1f}s  {content!r}')
            results['ok'].append(model_id)
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        body_text = e.read().decode('utf-8', errors='replace')[:200] if e.fp else ''
        # Try to extract the error message
        err_msg = body_text
        try:
            err_data = json.loads(body_text)
            if isinstance(err_data, dict):
                if 'error' in err_data:
                    inner = err_data['error']
                    err_msg = inner.get('message', str(inner)) if isinstance(inner, dict) else str(inner)
                elif 'detail' in err_data:
                    err_msg = err_data['detail']
                elif 'title' in err_data:
                    err_msg = f\"{err_data.get('title', '')}: {err_data.get('detail', '')}\"
        except (json.JSONDecodeError, ValueError):
            pass
        status = f'✗ {e.code}'
        print(f'  {model_id:<55} {status:<8} {elapsed:>5.1f}s  {err_msg[:60]}')
        results['error'].append((model_id, e.code, err_msg))
    except urllib.error.URLError as e:
        elapsed = time.time() - t0
        print(f'  {model_id:<55} {\"NET\":<8} {elapsed:>5.1f}s  {e.reason}')
        results['error'].append((model_id, 'NET', str(e.reason)))
    except Exception as e:
        elapsed = time.time() - t0
        print(f'  {model_id:<55} {\"ERR\":<8} {elapsed:>5.1f}s  {type(e).__name__}: {e}')
        results['error'].append((model_id, 'ERR', str(e)))

# Summary
print()
print(f'  Summary:')
print(f'    ✓ OK (200):           {len(results[\"ok\"])} models')
print(f'    ✗ Error (4xx/5xx):    {len(results[\"error\"])} models')
if results['error']:
    # Categorize errors
    not_found = [m for m, c, msg in results['error'] if c == 404]
    fixed_param = [m for m, c, msg in results['error'] if c == 400 and ('fixed' in msg.lower() or 'not supported' in msg.lower())]
    other_400 = [m for m, c, msg in results['error'] if c == 400 and m not in fixed_param]
    other = [m for m, c, msg in results['error'] if c not in (400, 404)]
    if not_found:
        print(f'      404 (no access):     {len(not_found)} — account lacks permission or model ID wrong')
        for m in not_found[:5]:
            print(f'        - {m}')
    if fixed_param:
        print(f'      400 (fixed param):   {len(fixed_param)} — top_p or other param fixed per model')
        for m in fixed_param[:5]:
            print(f'        - {m}')
    if other_400:
        print(f'      400 (other):         {len(other_400)} — validation error')
        for m in other_400[:5]:
            print(f'        - {m}')
    if other:
        print(f'      Other errors:        {len(other)}')

# Save detailed results
with open('/tmp/agentkthx_probe_nvidia_deep.json', 'w') as f:
    json.dump({
        'ok': results['ok'],
        'error': [{'model': m, 'status': c, 'message': msg} for m, c, msg in results['error']],
    }, f, indent=2, ensure_ascii=False)
print()
print(f'  Detailed results: /tmp/agentkthx_probe_nvidia_deep.json')
"
