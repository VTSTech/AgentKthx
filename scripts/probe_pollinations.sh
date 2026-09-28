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

# Free (zero-priced) models — the FREE_ONLY tier.
# NOTE: the live feed NEVER uses zero-valued price fields; true zero-cost
# models are encoded as a currency-only pricing dict {"currency":"pollen"}
# with NO price fields at all (the ':free'/'-free' community variants).
free = [m["id"] for m in models
        if set((m.get("pricing") or {}).keys()) <= {"currency"}
        or (m.get("pricing", {}).get("promptTextTokens") in ("0", 0)
            and m.get("pricing", {}).get("completionTextTokens") in ("0", 0))]
free_text = [m for m in models
             if m.get("category") in ("text", None) and m["id"] in free]
print(f"  Zero-cost models (any category):  {len(free)}")
print(f"  Zero-cost TEXT models:            {len(free_text)}")

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

# ─────────────────────────────────────────────────────────────────────────────
# Free-model discovery — the bare GET /models endpoint (public, anonymous,
# name-keyed cards) is the ONLY place the free/paid boundary is encoded:
#   paid_only: True  = paid tier (needs real pollen)
#   paid_only: False/missing = free TIER (Quest-Pollen-eligible — still
#              metered pollen per token, but runnable on the free grant)
#   pricing == {"currency": ...} only = TRUE zero cost (no price fields)
# This is what enter.pollinations.ai/models browses with source:community.
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'─'*70}")
print("→ GET https://gen.pollinations.ai/models (bare, anonymous) ...")
try:
    import urllib.request
    _req = urllib.request.Request(
        "https://gen.pollinations.ai/models",
        headers={"User-Agent": "AgentKthx-probe/0.x", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(_req, timeout=15) as _r:
        bare = json.loads(_r.read().decode("utf-8"))
    print(f"✓ {len(bare)} cards (name-keyed, rich schema)")
except Exception as e:
    bare = []
    print(f"✗ bare /models unreachable ({e}) — skipping free-tier discovery")

if bare:
    po_t = sum(1 for m in bare if m.get("paid_only") is True)
    po_f = sum(1 for m in bare if m.get("paid_only") is False)
    po_m = sum(1 for m in bare if "paid_only" not in m)
    print(f"\n  paid_only: True={po_t}  False={po_f}  missing={po_m}")
    print(f"  free TIER (not paid_only, any category): {po_f + po_m}")

    ft = [m for m in bare
          if m.get("paid_only") is not True and m.get("category") == "text"]
    comm_ft = [m for m in ft if m.get("community")]
    print(f"  free TIER text models:                   {len(ft)}")
    print(f"  free TIER text + community:              {len(comm_ft)}"
          f"   ← the website's source:community free view")

    def _sr(m):
        v = (m.get("health") or {}).get("success_rate")
        return v if v is not None else -1

    zero = [m for m in ft
            if set((m.get("pricing") or {}).keys()) <= {"currency"}]
    zero.sort(key=lambda m: (-_sr(m), m.get("name", "")))
    print(f"\n  TRUE zero-cost text models ({len(zero)}), health-ordered:")
    for m in zero:
        h = m.get("health") or {}
        s = h.get("success_rate")
        s = f"{s:g}" if s is not None else "?"
        print(f"    {m.get('name','?'):<56} sr={s:<8} "
              f"ctx={str(m.get('context_length')):<8} "
              f"tools={str(m.get('tools'))}")

    top = sorted(comm_ft, key=lambda m: (-_sr(m), m.get("name", "")))[:10]
    print(f"\n  healthiest free-TIER community text models (top 10 of {len(comm_ft)}):")
    for m in top:
        h = m.get("health") or {}
        s = h.get("success_rate")
        s = f"{s:g}" if s is not None else "?"
        print(f"    {m.get('name','?'):<56} sr={s:<8} "
              f"ctx={str(m.get('context_length')):<8} "
              f"tools={str(m.get('tools'))}")

    import os as _os
    if _os.environ.get("POLLINATIONS_API_KEY", "").strip():
        print("\n  NOTE: a key was used for /v1/models above — compare its card")
        print(f"        count against the free-TIER count ({po_f + po_m});")
        print("        the keyed feed IS the entitlement (= free-tier) subset.")

print(f"\n{'─'*70}")
print("  ✓ Probe complete — no pollen burned (GET-only)")
PYEOF
