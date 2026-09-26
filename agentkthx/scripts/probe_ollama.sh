#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_ollama.sh — Validate Ollama Backend
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (local, free — no tokens, no paid calls).
# Requires: Ollama running on localhost:11434 (or OLLAMA_BASE_URL set)
# Usage:  bash probe_ollama.sh
# Output: /tmp/agentkthx_probe_ollama.json
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
echo -e "${CYAN}${BOLD}═══ Ollama Backend Validation Probe ═══${NC}"
echo ""

echo "→ GET ${BASE_URL}/api/tags ..."
HTTP_CODE=$(curl -s -m 5 -o /tmp/agentkthx_probe_ollama.json -w "%{http_code}" \
    "${BASE_URL}/api/tags" 2>/dev/null || echo "000")

if [ "$HTTP_CODE" != "200" ]; then
    echo -e "${YELLOW}⚠ HTTP $HTTP_CODE — Ollama not running at ${BASE_URL}${NC}"
    echo "  Start it: ollama serve"
    echo "  Or set OLLAMA_BASE_URL: export OLLAMA_BASE_URL=http://your-host:11434"
    exit 1
fi
echo -e "${GREEN}✓ Response received${NC}"

python3 << 'PYEOF'
import json

d = json.load(open("/tmp/agentkthx_probe_ollama.json"))
models = d.get("models", [])
print(f"\n{'─'*70}")
print(f"  Total models installed:       {len(models)}")
print(f"  Response top-level keys:      {list(d.keys())}")
if models:
    print(f"  Sample model keys:            {list(models[0].keys())}")

# Ollama exposes: name, size, digest, details{parameter_size, quantization_level, family}
for m in models[:20]:
    name = m.get("name", "?")
    size_mb = m.get("size", 0) / 1024 / 1024
    details = m.get("details", {})
    params = details.get("parameter_size", "?")
    quant = details.get("quantization_level", "?")
    family = details.get("family", "?")
    print(f"  {name:40s}  {size_mb:8.1f}MB  {params:12s}  {quant:8s}  family={family}")

if len(models) > 20:
    print(f"  ... and {len(models) - 20} more")

# Check which fields are available
has_size = sum(1 for m in models if "size" in m)
has_details = sum(1 for m in models if "details" in m)
has_family = sum(1 for m in models if m.get("details", {}).get("family"))
print(f"\n  Field availability:")
print(f"    models with 'size':           {has_size}/{len(models)}")
print(f"    models with 'details':        {has_details}/{len(models)}")
print(f"    models with 'details.family':  {has_family}/{len(models)}")
print(f"\n  Note: Ollama is local and free — no pricing data. Context")
print(f"  length is determined at runtime by Modelfile num_ctx (default 2048).")

print(f"\n{'─'*70}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_ollama.json")
print(f"{'─'*70}")
PYEOF
