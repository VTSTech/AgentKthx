#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_pollinations.sh — Validate Pollinations API Technical Reference
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls, no pollen burned).
# POLLINATIONS_API_KEY optional — /v1/models is public/anonymous.
# Usage:  bash probe_pollinations.sh
# Output: /tmp/agentkthx_probe_pollinations.json
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

CYAN='\033[36m'; BOLD='\033[1m'; GREEN='\033[32m'; NC='\033[0m'
BASE="${POLLINATIONS_BASE_URL:-https://gen.pollinations.ai/v1}"

echo -e "${CYAN}${BOLD}═══ Pollinations API Technical Reference Validation Probe ═══${NC}"
echo ""

echo "→ GET $BASE/models ..."
if [ -n "${POLLINATIONS_API_KEY:-}" ]; then
    curl -s -m 15 \
        -H "User-Agent: AgentKthx-probe/0.x" \
        -H "Authorization: Bearer $POLLINATIONS_API_KEY" \
        "$BASE/models" > /tmp/agentkthx_probe_pollinations.json 2>/dev/null
else
    echo "  (keyless — the catalog endpoint is public)"
    curl -s -m 15 \
        -H "User-Agent: AgentKthx-probe/0.x" \
        "$BASE/models" > /tmp/agentkthx_probe_pollinations.json 2>/dev/null
fi

python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_pollinations.json')); assert 'data' in d" 2>/dev/null || {
    echo "ERROR: Failed to fetch /models"; cat /tmp/agentkthx_probe_pollinations.json | head -5; exit 1
}
echo -e "${GREEN}✓ Response received${NC}"

python3 << 'PYEOF'
import json
from collections import Counter

d = json.load(open("/tmp/agentkthx_probe_pollinations.json"))
models = d.get("data", [])
print(f"\n{'─'*70}")
print(f"  Total models in live API:     {len(models)}")
print(f"  Response top-level keys:      {list(d.keys())}")
print(f"  Sample model object keys:     {list(models[0].keys()) if models else 'N/A'}")

# Pollinations card shape: id, aliases, category, community, input/output
# modalities, supported_endpoints, pricing (per-token pollen strings),
# capabilities, tools, reasoning, context_length, health{status,
# success_rate, requests}
cats = Counter(m.get("category", "?") for m in models)
print(f"  By category:                  {dict(cats)}")
health_count = sum(1 for m in models if "health" in m)
ctx_count = sum(1 for m in models if "context_length" in m)
tools_count = sum(1 for m in models if m.get("tools"))
community_count = sum(1 for m in models if m.get("community"))
print(f"  Cards with health telemetry:  {health_count}")
print(f"  Cards with context_length:    {ctx_count}")
print(f"  Tool-capable cards:           {tools_count}")
print(f"  Community models:             {community_count}")
print(f"  Reference expects 311 cards / text-category present: ", "text" in cats)

# The platform default must exist and carry the card-verified 400K context
nano = next((m for m in models if m.get("id") == "openai/gpt-5.4-nano"), None)
if nano:
    print(f"\n  openai/gpt-5.4-nano card:")
    print(f"    context_length:  {nano.get('context_length')}")
    print(f"    aliases:          {nano.get('aliases')}")
    print(f"    capabilities:     {nano.get('capabilities')}")
    print(f"    health:           {nano.get('health')}")
    print(f"    pricing:          {nano.get('pricing')}")
else:
    print("\n  ⚠ openai/gpt-5.4-nano not found — platform default changed?")

# Verify provider-prefixed ids (the OpenRouter-style pattern the backend
# normalizes to)
slash_ids = sum(1 for m in models if "/" in m.get("id", ""))
print(f"\n  provider/model ids:           {slash_ids}/{len(models)}")

# Free (zero-priced) models — the FREE_ONLY tier
free = [m["id"] for m in models
        if m.get("pricing", {}).get("promptTextTokens") in ("0", 0)
        and m.get("pricing", {}).get("completionTextTokens") in ("0", 0)]
print(f"  Zero-priced models:           {len(free)}")

# Top-3 healthy text models — what healthy_fallbacks() would pick
text = [m for m in models
        if m.get("category") == "text"
        and not m.get("community")
        and m.get("tools")]
def price(m):
    try: return float(m.get("pricing", {}).get("promptTextTokens", 1) or 1)
    except (TypeError, ValueError): return 1.0
text.sort(key=lambda m: (-(m.get("health") or {}).get("success_rate", 0), price(m)))
print(f"\n  healthy_fallbacks() top-3 right now:")
for m in text[:3]:
    h = m.get("health") or {}
    print(f"    {m['id']:<40} success_rate={h.get('success_rate')}")

print(f"\n{'─'*70}")
print("  ✓ Probe complete — no pollen burned (GET-only)")
PYEOF
