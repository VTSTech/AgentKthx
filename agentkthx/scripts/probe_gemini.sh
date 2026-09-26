#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_gemini.sh — Validate Gemini API Technical Reference
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls, no tokens burned).
# Requires: GEMINI_API_KEY in env (or GOOGLE_API_KEY)
# Usage:  bash probe_gemini.sh
# Output: /tmp/agentkthx_probe_gemini.json
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

API_KEY="${GEMINI_API_KEY:-${GOOGLE_API_KEY:-}}"
[ -z "$API_KEY" ] && echo "ERROR: Set GEMINI_API_KEY first" >&2 && exit 1

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; NC='\033[0m'
echo -e "${CYAN}${BOLD}═══ Gemini API Technical Reference Validation Probe ═══${NC}"
echo ""

echo "→ GET https://generativelanguage.googleapis.com/v1beta/openai/models ..."
curl -s -m 15 \
    -H "Authorization: Bearer $API_KEY" \
    https://generativelanguage.googleapis.com/v1beta/openai/models \
    > /tmp/agentkthx_probe_gemini.json 2>/dev/null

python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_gemini.json')); assert 'data' in d" 2>/dev/null || {
    echo "ERROR: Failed to fetch /models"; cat /tmp/agentkthx_probe_gemini.json | head -5; exit 1
}
echo -e "${GREEN}✓ Response received${NC}"

python3 << 'PYEOF'
import json
from collections import Counter

d = json.load(open("/tmp/agentkthx_probe_gemini.json"))
models = d.get("data", [])
print(f"\n{'─'*70}")
print(f"  Total models in live API:     {len(models)}")
print(f"  Response top-level keys:      {list(d.keys())}")
print(f"  Sample model object keys:     {list(models[0].keys()) if models else 'N/A'}")

# Gemini /v1/models returns minimal metadata: id, object, created, owned_by
pricing_count = sum(1 for m in models if "pricing" in m)
ctx_count = sum(1 for m in models if "context_length" in m)
print(f"\n  Field availability:")
print(f"    models with 'pricing':        {pricing_count}/{len(models)}")
print(f"    models with 'context_length': {ctx_count}/{len(models)}")
if pricing_count == 0:
    print(f"    → Pricing NOT exposed (free-tier data transcribed manually from AI Studio)")
if ctx_count == 0:
    print(f"    → context_length NOT exposed (static catalog is the source of truth)")

# Model families — expanded patterns (R07.03)
families = Counter()
for m in models:
    mid = m.get("id", "").lower()
    if mid.startswith("gemini-3"):
        families["gemini-3"] += 1
    elif mid.startswith("gemini-2.5"):
        families["gemini-2.5"] += 1
    elif mid.startswith("gemini-2.0"):
        families["gemini-2.0"] += 1
    elif mid.startswith("gemma"):
        families["gemma"] += 1
    elif "embedding" in mid:
        families["embedding"] += 1
    elif "veo" in mid:
        families["veo (video)"] += 1
    elif "lyria" in mid:
        families["lyria (music)"] += 1
    elif "deep-research" in mid:
        families["deep-research"] += 1
    elif "antigravity" in mid:
        families["antigravity"] += 1
    elif "aqa" in mid:
        families["aqa (answer quality)"] += 1
    elif "computer-use" in mid:
        families["computer-use"] += 1
    elif "live" in mid or "transcribe" in mid or "translate" in mid:
        families["live/asr"] += 1
    elif "imagen" in mid or "image" in mid:
        families["imagen (image gen)"] += 1
    elif "robotics" in mid:
        families["robotics"] += 1
    elif "omni" in mid:
        families["omni (video)"] += 1
    else:
        families["other"] += 1
print(f"\n  Model family distribution:  {dict(families)}")

# Print ALL model IDs so we can see what the "other" category contains
print(f"\n  All {len(models)} model IDs:")
for m in sorted(models, key=lambda x: x.get("id", "")):
    mid = m.get("id", "?")
    display = m.get("display_name", m.get("owned_by", ""))
    print(f"    {mid:55s}  ({display})")

print(f"\n{'─'*70}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_gemini.json")
print(f"{'─'*70}")
PYEOF
