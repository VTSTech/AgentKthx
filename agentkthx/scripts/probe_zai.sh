#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_zai.sh — Validate ZAI API Technical Reference
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls, no tokens burned).
# Requires: ZAI_API_KEY in env
# Usage:  bash probe_zai.sh
# Note:   ZAI may not expose a /models endpoint — this script probes
#         both /api/paas/v4/models and /paas/v4/models to check.
# Output: /tmp/agentkthx_probe_zai.json
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

[ -z "${ZAI_API_KEY:-}" ] && echo "ERROR: Set ZAI_API_KEY first" >&2 && exit 1

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
echo -e "${CYAN}${BOLD}═══ ZAI API Technical Reference Validation Probe ═══${NC}"
echo ""

# ZAI doesn't have a documented /models endpoint. Try both paths.
echo "→ Probing ZAI model-discovery endpoints (GET — no tokens burned) ..."

for path in "/api/paas/v4/models" "/paas/v4/models"; do
    URL="https://api.z.ai${path}"
    echo "  Trying: $URL"
    HTTP_CODE=$(curl -s -m 10 -o /tmp/agentkthx_probe_zai.json -w "%{http_code}" \
        -H "Authorization: Bearer $ZAI_API_KEY" \
        -H "Content-Type: application/json" \
        "$URL" 2>/dev/null || echo "000")

    if [ "$HTTP_CODE" = "200" ]; then
        echo -e "  ${GREEN}✓ HTTP 200 — endpoint exists!${NC}"
        python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_zai.json')); assert 'data' in d or 'models' in d or isinstance(d, list)" 2>/dev/null && {
            echo -e "  ${GREEN}✓ Valid JSON model list received${NC}"
            python3 << 'PYEOF'
import json
d = json.load(open("/tmp/agentkthx_probe_zai.json"))
models = d.get("data", d.get("models", d if isinstance(d, list) else []))
print(f"\n{'─'*70}")
print(f"  Total models in ZAI API:  {len(models)}")
if models:
    print(f"  Sample model keys:        {list(models[0].keys()) if isinstance(models[0], dict) else models[0]}")
    for m in models[:20]:
        mid = m.get("id", m.get("name", "?")) if isinstance(m, dict) else str(m)
        print(f"    - {mid}")
print(f"\n{'─'*70}")
PYEOF
            break
        } || {
            echo -e "  ${YELLOW}⚠ HTTP 200 but not a model list. Body:${NC}"
            head -c 500 /tmp/agentkthx_probe_zai.json
            echo ""
        }
    elif [ "$HTTP_CODE" = "404" ] || [ "$HTTP_CODE" = "401" ]; then
        echo -e "  ${YELLOW}⚠ HTTP $HTTP_CODE — endpoint not available${NC}"
    else
        echo -e "  ${YELLOW}⚠ HTTP $HTTP_CODE${NC}"
    fi
done

# Also verify auth by hitting the base URL
echo ""
echo "→ Verifying auth with ZAI base URL ..."
HTTP_CODE=$(curl -s -m 5 -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $ZAI_API_KEY" \
    "https://api.z.ai" 2>/dev/null || echo "000")
echo "  ZAI base URL: HTTP $HTTP_CODE"

# Static catalog reference
echo ""
echo "Static ZAI_MODELS catalog (from zai.py):"
python3 << 'PYEOF'
# Print the known ZAI models from the AgentKthx source
ZAI_MODELS_KNOWN = [
    "glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-5.1", "glm-5",
    "glm-4.5", "glm-4.5-flash", "glm-4.5-air", "glm-4-plus",
    "glm-4-air", "glm-4-flash", "glm-4", "glm-4-long",
    "glm-4-flashx", "glm-4-flashx-glm",
    "glm-z1-air", "glm-z1-flash", "glm-z1-32b-0414",
]
for m in ZAI_MODELS_KNOWN:
    print(f"  - {m}")
print(f"\n  Catalog size: {len(ZAI_MODELS_KNOWN)}")
print(f"  Note: ZAI doesn't expose a /models endpoint — the static")
print(f"  catalog in zai.py is the source of truth. Pricing data is")
print(f"  not exposed via API either — see docs/ZAI_API_TECHNICAL_")
print(f"  REFERENCE.md for the curated model list with context_length")
print(f"  and max_tokens info.")
PYEOF

echo -e "\n${YELLOW}Note: ZAI's /models endpoint is not publicly documented.${NC}"
echo "The static ZAI_MODELS catalog in agentkthx/plugins/zai/zai.py"
echo "is the source of truth for model discovery."
echo ""
echo "Raw response saved: /tmp/agentkthx_probe_zai.json"
