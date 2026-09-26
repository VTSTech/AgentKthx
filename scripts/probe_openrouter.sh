#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_openrouter.sh — Validate OpenRouter API Technical Reference
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls, no tokens burned).
# OPENROUTER_API_KEY optional — /models is anonymous.
# Usage:  bash probe_openrouter.sh
# Output: /tmp/agentkthx_probe_openrouter.json
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; NC='\033[0m'
echo -e "${CYAN}${BOLD}═══ OpenRouter API Technical Reference Validation Probe ═══${NC}"
echo ""

echo "→ GET https://openrouter.ai/api/v1/models ..."
curl -s -m 15 \
    -H "User-Agent: AgentKthx-probe/0.x" \
    https://openrouter.ai/api/v1/models > /tmp/agentkthx_probe_openrouter.json 2>/dev/null

python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_openrouter.json')); assert 'data' in d" 2>/dev/null || {
    echo "ERROR: Failed to fetch /models"; cat /tmp/agentkthx_probe_openrouter.json | head -5; exit 1
}
echo -e "${GREEN}✓ Response received${NC}"

python3 << 'PYEOF'
import json
from collections import Counter

d = json.load(open("/tmp/agentkthx_probe_openrouter.json"))
models = d.get("data", [])
print(f"\n{'─'*70}")
print(f"  Total models in live API:     {len(models)}")
print(f"  Response top-level keys:      {list(d.keys())}")
print(f"  Sample model object keys:     {list(models[0].keys()) if models else 'N/A'}")

# OpenRouter exposes: id, context_length, pricing{prompt,completion,...},
# top_provider{max_completion_tokens}, supported_parameters, architecture{modality}
pricing_count = sum(1 for m in models if "pricing" in m)
ctx_count = sum(1 for m in models if "context_length" in m)
maxout_count = sum(1 for m in models if m.get("top_provider", {}).get("max_completion_tokens"))
params_count = sum(1 for m in models if "supported_parameters" in m)
arch_count = sum(1 for m in models if "architecture" in m)

print(f"\n  Field availability:")
print(f"    models with 'pricing':                  {pricing_count}/{len(models)}")
print(f"    models with 'context_length':           {ctx_count}/{len(models)}")
print(f"    models with 'top_provider.max_completion_tokens': {maxout_count}/{len(models)}")
print(f"    models with 'supported_parameters':      {params_count}/{len(models)}")
print(f"    models with 'architecture':             {arch_count}/{len(models)}")

# Free models (:free suffix)
free_models = [m for m in models if m.get("id", "").endswith(":free")]
paid_models = [m for m in models if not m.get("id", "").endswith(":free")]
print(f"\n  Free models (:free suffix):   {len(free_models)}")
print(f"  Paid models:                  {len(paid_models)}")

# Pricing analysis (per 1M tokens, USD)
print(f"\n  Cheapest input rates (USD per 1M tokens):")
priced = []
for m in models:
    pr = m.get("pricing", {})
    if pr.get("prompt"):
        priced.append((m["id"], float(pr["prompt"]), float(pr.get("completion", 0))))
priced.sort(key=lambda x: x[1])
for mid, in_r, out_r in priced[:10]:
    print(f"    {mid:55s}  in=${in_r:.4f}/1M  out=${out_r:.4f}/1M")

# Context length analysis
print(f"\n  Top 10 longest context:")
ctx_list = [(m["id"], m.get("context_length", 0)) for m in models if m.get("context_length")]
ctx_list.sort(key=lambda x: x[1], reverse=True)
for mid, ctx in ctx_list[:10]:
    print(f"    {mid:55s}  {ctx:>10,} tokens")

# Modality distribution
modality = Counter(m.get("architecture", {}).get("modality", "?") for m in models)
print(f"\n  Modality distribution:  {dict(modality)}")

print(f"\n{'─'*70}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_openrouter.json")
print(f"{'─'*70}")
PYEOF
