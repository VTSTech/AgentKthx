#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_openai.sh — Validate OpenAI API Technical Reference
# ═══════════════════════════════════════════════════════════════════════════
# GET-only probe (no inference calls, no tokens burned).
# Requires: OPENAI_API_KEY in env (sk-proj- recommended)
# Usage:    bash probe_openai.sh
# Output:   /tmp/agentkthx_probe_openai.json  (raw API response)
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

[ -z "${OPENAI_API_KEY:-}" ] && echo "ERROR: Set OPENAI_API_KEY first: export OPENAI_API_KEY=sk-proj-..." >&2 && exit 1

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
echo -e "${CYAN}${BOLD}═══ OpenAI API Technical Reference Validation Probe ═══${NC}"
echo ""

# 1. Probe /v1/models (GET — no tokens burned)
echo "→ GET https://api.openai.com/v1/models ..."
curl -s -m 15 \
    -H "Authorization: Bearer $OPENAI_API_KEY" \
    -H "User-Agent: AgentKthx-probe/0.x" \
    https://api.openai.com/v1/models > /tmp/agentkthx_probe_openai.json 2>/dev/null

HTTP_OK=$(python3 -c "import json; d=json.load(open('/tmp/agentkthx_probe_openai.json')); print('ok' if 'data' in d else 'err')")
if [ "$HTTP_OK" = "err" ]; then
    echo -e "${RED}Failed to fetch /v1/models. Response:${NC}"
    cat /tmp/agentkthx_probe_openai.json | head -5
    exit 1
fi
echo -e "${GREEN}✓ Response received${NC}"

# 2. Parse and analyze
python3 << 'PYEOF'
import json, sys
from collections import Counter

d = json.load(open("/tmp/agentkthx_probe_openai.json"))
models = d.get("data", [])
print(f"\n{'─'*70}")
print(f"  Total models in live API:     {len(models)}")
print(f"  Response top-level keys:      {list(d.keys())}")
print(f"  Sample model object keys:     {list(models[0].keys()) if models else 'N/A'}")
print(f"  Sample model:                 {models[0].get('id','?') if models else 'N/A'}")
print(f"  Owned by distribution:        {dict(Counter(m.get('owned_by','?') for m in models))}")
print(f"{'─'*70}")

# Check what fields are exposed (the API spec says only id/object/created/owned_by)
# Are pricing, context_length, or max_output_tokens exposed?
pricing_count = sum(1 for m in models if "pricing" in m)
ctx_count = sum(1 for m in models if "context_length" in m)
maxout_count = sum(1 for m in models if "max_output_tokens" in m or "max_completion_tokens" in m)
print(f"\n  Models with 'pricing' field:        {pricing_count}/{len(models)}")
print(f"  Models with 'context_length':       {ctx_count}/{len(models)}")
print(f"  Models with 'max_output_tokens':    {maxout_count}/{len(models)}")
if pricing_count == 0:
    print(f"  → Pricing NOT exposed by /v1/models (expected — use platform.openai.com/docs/pricing)")
if ctx_count == 0:
    print(f"  → context_length NOT exposed (static catalog is the source of truth)")
if maxout_count == 0:
    print(f"  → max_output_tokens NOT exposed (static catalog is the source of truth)")

# Non-chat filter (mirrors _NON_CHAT_PATTERNS in openai.py)
NON_CHAT = ("embedding", "tts", "transcribe", "whisper", "image", "sora", "moderation", "babbage", "davinci")
chat = [m for m in models if not any(p in m.get("id","").lower() for p in NON_CHAT)]
non_chat = [m for m in models if any(p in m.get("id","").lower() for p in NON_CHAT)]
print(f"\n  Chat-capable models (after _NON_CHAT_PATTERNS filter): {len(chat)}")
print(f"  Non-chat models filtered:                             {len(non_chat)}")
print(f"  Non-chat breakdown:")
nc_patterns = Counter()
for m in non_chat:
    for p in NON_CHAT:
        if p in m.get("id","").lower():
            nc_patterns[p] += 1
            break
for p, c in sorted(nc_patterns.items()):
    print(f"    {p:15s} → {c} models")

# Compare against static catalog
CATALOG = {
    "gpt-6-astra","gpt-6-sol","gpt-6-luna",
    "gpt-5.6-sol","gpt-5.6-cyber","gpt-5.6-luna","gpt-5.6-terra",
    "gpt-5.5","gpt-5.5-pro","gpt-5.4","gpt-5.4-mini","gpt-5.4-nano","gpt-5.4-pro",
    "gpt-5.3-codex","gpt-5","gpt-5-mini","gpt-5-nano","gpt-5-pro","gpt-5-codex",
    "gpt-5.1","gpt-5.1-codex","gpt-5.2","gpt-5.2-pro",
    "gpt-4o","gpt-4o-mini","gpt-4.1","gpt-4.1-mini","gpt-4.1-nano",
    "gpt-3.5-turbo","gpt-3.5-turbo-16k",
    "o1","o3","o3-mini","o4-mini",
    "chat-latest","gpt-rosalind-research",
    "gpt-realtime-2.1","gpt-realtime-2.1-mini",
}
live_ids = {m["id"] for m in chat}
in_live_not_catalog = sorted(live_ids - CATALOG)
in_catalog_not_live = sorted(CATALOG - live_ids)
print(f"\n  Static OPENAI_MODELS catalog size:  {len(CATALOG)}")
print(f"  In live API but NOT in catalog:     {len(in_live_not_catalog)} models")
if in_live_not_catalog[:15]:
    for m in in_live_not_catalog[:15]:
        print(f"    + {m}")
    if len(in_live_not_catalog) > 15:
        print(f"    ... and {len(in_live_not_chat) - 15} more" if 'in_live_not_chat' in dir() else f"    ... and {len(in_live_not_catalog) - 15} more")
print(f"  In catalog but NOT in live API:     {len(in_catalog_not_live)} models")
for m in in_catalog_not_live:
    print(f"    - {m}")

# Whitelist check
WHITELIST = {"gpt-6-luna","gpt-4o-mini","gpt-4.1-mini","gpt-realtime-2.1-mini","gpt-4o-mini-transcribe","gpt-transcribe"}
print(f"\n  OPENAI_FREE_MODEL_WHITELIST size:   {len(WHITELIST)}")
whitelist_in_live = sorted(WHITELIST & live_ids)
whitelist_missing = sorted(WHITELIST - live_ids)
print(f"  Whitelist models in live API:        {len(whitelist_in_live)}")
for m in whitelist_in_live:
    print(f"    ✓ {m}")
if whitelist_missing:
    print(f"  Whitelist models NOT in live API:   {len(whitelist_missing)}")
    for m in whitelist_missing:
        print(f"    ✗ {m}")

print(f"\n{'─'*70}")
print(f"  Raw JSON saved: /tmp/agentkthx_probe_openai.json")
print(f"{'─'*70}")
PYEOF
