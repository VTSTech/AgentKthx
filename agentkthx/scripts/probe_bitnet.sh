#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_bitnet.sh — Validate BitNet Backend
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (local, free — no tokens, no paid calls).
# BitNet runs on llama-server with bitnet_mode=True — same /v1/models endpoint.
# Requires: BitNet server running (agentkthx turbo start or manual llama-server)
# Usage:  bash probe_bitnet.sh
# Output: /tmp/agentkthx_probe_bitnet.json
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

BASE_URL="${BITNET_BASE_URL:-${BITNET_TUNNEL:-http://localhost:8765}}"

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
echo -e "${CYAN}${BOLD}═══ BitNet Backend Validation Probe ═══${NC}"
echo ""

echo "→ GET ${BASE_URL}/v1/models ..."
HTTP_CODE=$(curl -s -m 5 -o /tmp/agentkthx_probe_bitnet.json -w "%{http_code}" \
    "${BASE_URL}/v1/models" 2>/dev/null || echo "000")

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "${YELLOW}⚠ HTTP $HTTP_CODE — BitNet server not running at ${BASE_URL}${NC}"
    echo "  Start it: agentkthx turbo start bitnet-b1.58-2b-4t"
    echo "  Or set BITNET_BASE_URL"
    exit 1
fi
echo -e "${GREEN}✓ Response received${NC}"

python3 << 'PYEOF'
import json

d = json.load(open("/tmp/agentkthx_probe_bitnet.json"))
models = d.get("data", [])
print(f"\n{'─'*70}")
print(f"  Total models loaded:          {len(models)}")
print(f"  Response top-level keys:      {list(d.keys())}")
if models:
    print(f"  Sample model keys:            {list(models[0].keys())}")

for m in models:
    mid = m.get("id", "?")
    print(f"  {mid}")

print(f"\n  Note: BitNet runs on llama-server with bitnet_mode=True.")
print(f"  Same /v1/models endpoint, same minimal metadata (id, object,")
print(f"  created, owned_by). No pricing (local, free). Context length")
print(f"  set at startup via --ctx-size.")

print(f"\n{'─'*70}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_bitnet.json")
print(f"{'─'*70}")
PYEOF
