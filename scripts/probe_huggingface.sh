#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_huggingface.sh — Validate Hugging Face API Technical Reference
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls, no tokens burned).
# HF_TOKEN optional — /v1/models is anonymous on HF Router.
# Usage:  bash probe_huggingface.sh
# Output: /tmp/agentkthx_probe_huggingface.json
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
echo -e "${CYAN}${BOLD}═══ Hugging Face API Technical Reference Validation Probe ═══${NC}"
echo ""

AUTH_HEADER=""
[ -n "${HF_TOKEN:-}" ] && AUTH_HEADER="-H \"Authorization: Bearer $HF_TOKEN\""

# 1. Probe /v1/models (GET — anonymous, no tokens burned)
echo "→ GET https://router.huggingface.co/v1/models ..."
if [ -n "${HF_TOKEN:-}" ]; then
    curl -s -m 15 \
        -H "Authorization: Bearer $HF_TOKEN" \
        -H "User-Agent: AgentKthx-probe/0.x" \
        https://router.huggingface.co/v1/models > /tmp/agentkthx_probe_huggingface.json 2>/dev/null
else
    curl -s -m 15 \
        -H "User-Agent: AgentKthx-probe/0.x" \
        https://router.huggingface.co/v1/models > /tmp/agentkthx_probe_huggingface.json 2>/dev/null
fi

HTTP_OK=$(python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_huggingface.json')); print('ok' if 'data' in d else 'err')")
if [ "$HTTP_OK" = "err" ]; then
    echo -e "${RED}Failed to fetch /v1/models. Response:${NC}"
    cat /tmp/agentkthx_probe_huggingface.json | head -5
    exit 1
fi
echo -e "${GREEN}✓ Response received${NC}"

# 2. Parse and analyze — HF Router returns rich per-provider data
python3 << 'PYEOF'
import json
from collections import Counter

d = json.load(open("/tmp/agentkthx_probe_huggingface.json"))
models = d.get("data", [])
print(f"\n{'─'*70}")
print(f"  Total models in live API:     {len(models)}")
print(f"  Response top-level keys:      {list(d.keys())}")
print(f"  Sample model object keys:     {list(models[0].keys()) if models else 'N/A'}")

# HF Router exposes per-provider data: pricing, context_length, is_free,
# supports_tools, supports_structured_output, first_token_latency_ms,
# throughput, status, is_model_author
providers_count = sum(len(m.get("providers", [])) for m in models)
total_combos = sum(len(m.get("providers", [])) for m in models)
pricing_combos = 0
ctx_combos = 0
is_free_combos = 0
tools_combos = 0
all_provider_names = set()
for m in models:
    for p in m.get("providers", []):
        all_provider_names.add(p.get("provider", "?"))
        if "pricing" in p:
            pricing_combos += 1
        if "context_length" in p:
            ctx_combos += 1
        if p.get("is_free") is not None:
            is_free_combos += 1
        if "supports_tools" in p:
            tools_combos += 1

print(f"  Total (model, provider) combos: {total_combos}")
print(f"  Unique partner providers:        {len(all_provider_names)}")
print(f"  Provider names:                  {', '.join(sorted(all_provider_names))}")
print(f"{'─'*70}")
print(f"\n  Per-provider field availability:")
print(f"    combos with 'pricing':           {pricing_combos}/{total_combos}")
print(f"    combos with 'context_length':     {ctx_combos}/{total_combos}")
print(f"    combos with 'is_free' flag:       {is_free_combos}/{total_combos}")
print(f"    combos with 'supports_tools':     {tools_combos}/{total_combos}")

# is_free analysis
free_combos = [(m["id"], p["provider"]) for m in models for p in m.get("providers", []) if p.get("is_free")]
print(f"\n  is_free=true combos:  {len(free_combos)}/{total_combos}")
if free_combos:
    for mid, prov in free_combos[:20]:
        print(f"    ✓ {mid:50s} via {prov}")
else:
    print(f"    (none — as of Sept 2026, free-tier is purely credit-based)")

# Pricing analysis (per 1M tokens, USD)
print(f"\n  Cheapest input rates (USD per 1M tokens):")
priced = []
for m in models:
    for p in m.get("providers", []):
        pr = p.get("pricing", {})
        if isinstance(pr.get("input"), (int, float)):
            priced.append((m["id"], p["provider"], pr["input"], pr.get("output", 0)))
priced.sort(key=lambda x: x[2])
for mid, prov, in_r, out_r in priced[:10]:
    print(f"    {mid:50s} via {prov:15s}  in=${in_r:.4f}/1M  out=${out_r:.4f}/1M")

# $0 pricing (genuinely free?)
zero_cost = [(mid, prov) for mid, prov, in_r, out_r in priced if in_r == 0 and out_r == 0]
print(f"\n  $0 input + $0 output combos:  {len(zero_cost)}")
for mid, prov in zero_cost[:10]:
    print(f"    $0  {mid:50s} via {prov}")

# Context length analysis
print(f"\n  Max context lengths per model (max across providers):")
ctx_list = []
for m in models:
    ctxs = [p.get("context_length") for p in m.get("providers", []) if isinstance(p.get("context_length"), int)]
    if ctxs:
        ctx_list.append((m["id"], max(ctxs)))
ctx_list.sort(key=lambda x: x[1], reverse=True)
print(f"    Top 10 longest context:")
for mid, ctx in ctx_list[:10]:
    print(f"      {mid:50s}  {ctx:>10,} tokens")

# Compare against static catalog
CATALOG = {
    "openai/gpt-oss-20b", "openai/gpt-oss-120b",
    "Qwen/Qwen3-4B-Thinking-2507", "Qwen/Qwen3-Coder-480B-A35B-Instruct",
    "Qwen/Qwen2.5-7B-Instruct-1M", "Qwen/Qwen2.5-Coder-32B-Instruct",
    "Qwen/Qwen2.5-72B-Instruct", "Qwen/Qwen2.5-Math-7B-Instruct",
    "deepseek-ai/DeepSeek-R1", "deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-V3.1",
    "meta-llama/Llama-3.3-70B-Instruct", "meta-llama/Llama-3.2-3B-Instruct",
    "meta-llama/Llama-3.2-1B-Instruct", "meta-llama/Llama-3.1-8B-Instruct",
    "google/gemma-2-2b-it", "google/gemma-2-9b-it", "google/gemma-3-4b-it",
    "google/gemma-3-12b-it", "google/gemma-3-27b-it",
    "mistralai/Mistral-7B-Instruct-v0.3", "mistralai/Mistral-Nemo-Instruct-2407",
    "mistralai/Mixtral-8x7B-Instruct-v0.1",
    "zai-org/GLM-4.5", "zai-org/GLM-4.5-Air", "zai-org/GLM-Z1-32B-0414",
    "microsoft/Phi-3.5-mini-instruct", "microsoft/Phi-3.5-MoE-instruct",
    "microsoft/Phi-4-mini-instruct",
    "CohereForAI/c4ai-command-r-plus-08-2024", "CohereForAI/c4ai-command-r-08-2024",
}
live_ids = {m["id"] for m in models}
in_live_not_catalog = sorted(live_ids - CATALOG)
in_catalog_not_live = sorted(CATALOG - live_ids)
print(f"\n  Static HF_MODELS catalog size:       {len(CATALOG)}")
print(f"  In live API but NOT in catalog:      {len(in_live_not_catalog)} models")
if in_live_not_catalog[:10]:
    for m in in_live_not_catalog[:10]:
        print(f"    + {m}")
    if len(in_live_not_catalog) > 10:
        print(f"    ... and {len(in_live_not_catalog) - 10} more")
print(f"  In catalog but NOT in live API:      {len(in_catalog_not_live)} models")
for m in in_catalog_not_live:
    print(f"    - {m}")

print(f"\n{'─'*70}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_huggingface.json")
print(f"{'─'*70}")
PYEOF
