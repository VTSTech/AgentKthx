#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_llama_server.sh — Validate llama-server Backend
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (local, free — no tokens, no paid calls).
# Requires: llama-server running on localhost:8764 (or LLAMA_SERVER_BASE_URL set)
# Usage:  bash probe_llama_server.sh
# Output: /tmp/agentkthx_probe_llama_server.json
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

BASE_URL="${LLAMA_SERVER_BASE_URL:-http://localhost:8764}"

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
echo -e "${CYAN}${BOLD}═══ llama-server Backend Validation Probe ═══${NC}"
echo ""

echo "→ GET ${BASE_URL}/v1/models ..."
HTTP_CODE=$(curl -s -m 5 -o /tmp/agentkthx_probe_llama_server.json -w "%{http_code}" \
    "${BASE_URL}/v1/models" 2>/dev/null || echo "000")

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "${YELLOW}⚠ HTTP $HTTP_CODE — llama-server not running at ${BASE_URL}${NC}"
    echo "  Start it: llama-server -m your-model.gguf --port 8764"
    echo "  Or set LLAMA_SERVER_BASE_URL"
    echo ""
    echo "  Also try the TurboQuant path:"
    echo "    agentkthx turbo start qwen2.5:7b"
    exit 1
fi
echo -e "${GREEN}✓ Response received${NC}"

python3 << 'PYEOF'
import json

d = json.load(open("/tmp/agentkthx_probe_llama_server.json"))
models = d.get("data", [])
print(f"\n{'─'*70}")
print(f"  Total models loaded:          {len(models)}")
print(f"  Response top-level keys:      {list(d.keys())}")
if models:
    print(f"  Sample model keys:            {list(models[0].keys())}")

for m in models:
    mid = m.get("id", "?")
    created = m.get("created", "?")
    owned = m.get("owned_by", "?")
    print(f"  {mid:40s}  owned_by={owned}")

print(f"\n  Note: llama-server is local and free — no pricing data.")
print(f"  Context length is set at startup via --ctx-size (default 2048).")
print(f"  Max output tokens set via --n-predict (default -1 = unlimited).")
print(f"  The /v1/models endpoint returns minimal metadata (id, object,")
print(f"  created, owned_by) — no context_length, no max_output_tokens.")

print(f"\n{'─'*70}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_llama_server.json")
print(f"{'─'*70}")
PYEOF
