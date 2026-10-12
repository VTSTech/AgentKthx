#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_tokenharbor.sh — Validate the TokenHarbor API Technical Reference
# ═══════════════════════════════════════════════════════════════════════════
# Sibling of probe_openrouter.sh (docs/api/TOKENHARBOR_API_TECHNICAL_
# REFERENCE.md §16 companion change #6). Four stages, in the doc's order:
#
#   1. Key check          — TOKENHARBOR_API_KEY presence + shape (thk_live_)
#   2. GET /v1/models     — AUTH-REQUIRED (docs-verified): expects 401
#                           keyless, 200 keyed. Captures the live catalog
#                           (id field only — the entry schema is the doc's
#                           probe checklist #1), the :free set, and seed
#                           drift vs model_seed.json.
#   3. One non-streaming  — POST /v1/chat/completions on the default :free
#      call                 model (never bills the wallet; max_tokens=32 so
#                           the spend gate can never trip). Answers the
#                           usage-field family + finish_reason + error-
#                           envelope probe questions (checklist #2/#3/#5).
#   4. One streaming call — validates the SSE grammar + whether a usage-
#                           bearing final chunk exists (checklist #4).
#
# TOKENHARBOR_API_KEY REQUIRED (the catalog read needs auth; the inference
# calls default to a :free model so they cost $0, but they still need a key).
# Usage:    bash probe_tokenharbor.sh
# Output:   /tmp/agentkthx_probe_tokenharbor.json  (raw API responses)
# ═══════════════════════════════════════════════════════════════════════════
set -uo pipefail

API_KEY="${TOKENHARBOR_API_KEY:-}"
BASE_URL="${TOKENHARBOR_BASE_URL:-https://tokenharbor.ai/v1}"
MODEL="${TOKENHARBOR_DEFAULT_MODEL:-mimo-v2.6-flash:free}"
OUT="/tmp/agentkthx_probe_tokenharbor.json"

CYAN='\033[0;36m'; BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

echo -e "${BOLD}TokenHarbor API probe — $(date -u +%Y-%m-%dT%H:%M:%SZ)${NC}"
echo -e "Base URL: ${CYAN}${BASE_URL}${NC}   Default model: ${CYAN}${MODEL}${NC}"
echo

mkdir -p /tmp

# ─── Stage 1: key check ────────────────────────────────────────────────────
echo -e "${BOLD}[1/4] Key check${NC}"
if [[ -z "$API_KEY" ]]; then
  echo -e "${RED}✗ TOKENHARBOR_API_KEY is not set.${NC}"
  echo -e "  Create a key at https://tokenharbor.ai/dashboard/api-keys"
  echo -e "  (thk_live_…, SHOWN ONCE — there is no management API)."
  echo -e "  The catalog read below is auth-gated; the probe cannot continue."
  exit 1
fi
if [[ "$API_KEY" == sk-* ]]; then
  echo -e "${RED}✗ TOKENHARBOR_API_KEY looks like an OpenAI/OpenRouter key (sk-…).${NC}"
  echo -e "  Token Harbor keys start with thk_live_ (their connect CLI docs:"
  echo -e "  an OpenAI sk- key \"won't work here\")."
  exit 1
fi
if [[ "$API_KEY" == thk_live_* ]]; then
  echo -e "${GREEN}✓${NC} thk_live_ key present (${#API_KEY} chars; documented shape: 73 = 9 + 64 base64url)"
else
  echo -e "${YELLOW}⚠${NC} key does not start with thk_live_ (got ${API_KEY:0:8}…) — continuing, but expect 401"
fi
echo

AUTH=(-H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json")

# ─── Stage 2: GET /v1/models (auth-gated) ─────────────────────────────────
echo -e "${BOLD}[2/4] GET /v1/models (auth required)${NC}"
HTTP_CODE=$(curl -sS -o /tmp/th_models.json -w "%{http_code}" -m 20 \
  "${BASE_URL}/models" "${AUTH[@]}" 2>/tmp/th_curl_err.txt) || HTTP_CODE="000"
if [[ "$HTTP_CODE" == "200" ]]; then
  COUNT=$(python3 -c "
import json
try:
    d = json.load(open('/tmp/th_models.json'))
    data = d.get('data', d if isinstance(d, list) else [])
    print(len(data))
except Exception:
    print('?')
")
  echo -e "${GREEN}✓${NC} HTTP 200 — ${COUNT} model entries"
  python3 - "$MODEL" <<'PYEOF'
import json, sys
d = json.load(open('/tmp/th_models.json'))
data = d.get("data", d if isinstance(d, list) else [])
ids = [m.get("id") if isinstance(m, dict) else str(m) for m in data]
free = sorted(i for i in ids if isinstance(i, str) and i.endswith(":free"))
print(f"  entry fields on first card: {sorted(data[0].keys()) if data and isinstance(data[0], dict) else '(non-dict entries)'}")
print(f"  :free set ({len(free)}): {free if free else '(none listed)'}")
default = sys.argv[1]
print(f"  default model {default!r} live: {default in ids}")
# Seed drift check vs the packaged docs-only seed
try:
    seed = json.load(open("agentkthx/data/model_seed.json")).get("tokenharbor", {})
    missing_live = sorted(set(seed) - set(ids))
    print(f"  seed ids absent from live catalog ({len(missing_live)}): {missing_live}")
except Exception as e:
    print(f"  (seed drift check skipped: {e})")
PYEOF
  echo -e "  ${YELLOW}NOTE: entry schema → doc §16 probe checklist #1 (fields above feed the seed refresh)${NC}"
else
  echo -e "${RED}✗ HTTP ${HTTP_CODE} on /v1/models${NC} $(cat /tmp/th_curl_err.txt 2>/dev/null)"
  if [[ "$HTTP_CODE" == "401" ]]; then
    echo -e "  Key rejected — was the account-wide Rotate used? Create a new key."
  fi
  echo -e "  Raw: $(head -c 400 /tmp/th_models.json 2>/dev/null)"
fi
echo

# ─── Stage 3: one non-streaming call (:free default, max_tokens=32) ───────
echo -e "${BOLD}[3/4] POST /v1/chat/completions (non-streaming, ${MODEL})${NC}"
REQ_BODY=$(python3 -c "
import json, os
print(json.dumps({
    'model': os.environ.get('PROBE_MODEL', 'mimo-v2.6-flash:free'),
    'messages': [{'role': 'user', 'content': 'Reply with the single word: pong'}],
    'max_tokens': 32,
    'stream': False,
}))
")
PROBE_MODEL="$MODEL" REQ_BODY="$REQ_BODY" python3 - <<'PYEOF' || echo -e "  ${RED}✗ non-streaming call failed (raw above)${NC}"
import json, os, urllib.error, urllib.request

body = os.environ["REQ_BODY"].encode()
req = urllib.request.Request(
    os.environ["BASE_URL"] + "/chat/completions",
    data=body,
    headers={
        "Authorization": "Bearer " + os.environ["TOKENHARBOR_API_KEY"],
        "Content-Type": "application/json",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = json.loads(resp.read().decode())
        print(f"  HTTP {resp.status}")
except urllib.error.HTTPError as e:
    payload = e.read().decode("utf-8", errors="replace")
    print(f"  HTTP {e.code} — raw body (error-envelope probe, checklist #3):")
    print("  " + payload[:600])
    raise SystemExit(1)

usage = raw.get("usage", {})
print(f"  finish_reason: {raw.get('choices', [{}])[0].get('finish_reason')!r}   (checklist #5)")
print(f"  usage keys: {sorted(usage.keys()) if usage else '(EMPTY — checklist #2)'}")
if raw.get("choices", [{}])[0].get("message", {}).get("reasoning_content"):
    print("  reasoning_content present (thinking delta signal)")
print(f"  cache headers: X-TH-Cache-Layer={resp.headers.get('X-TH-Cache-Layer')!r}")
with open("/tmp/agentkthx_probe_tokenharbor.json", "w") as f:
    json.dump({"non_streaming": raw}, f, indent=2)
PYEOF
echo

# ─── Stage 4: one streaming call ───────────────────────────────────────────
echo -e "${BOLD}[4/4] POST /v1/chat/completions (streaming)${NC}"
PROBE_MODEL="$MODEL" python3 - <<'PYEOF' || echo -e "  ${RED}✗ streaming call failed (raw above)${NC}"
import json, os, urllib.error, urllib.request

body = json.dumps({
    "model": os.environ.get("PROBE_MODEL", "mimo-v2.6-flash:free"),
    "messages": [{"role": "user", "content": "Count from 1 to 5, digits only."}],
    "max_tokens": 32,
    "stream": True,
}).encode()
req = urllib.request.Request(
    os.environ["BASE_URL"] + "/chat/completions",
    data=body,
    headers={
        "Authorization": "Bearer " + os.environ["TOKENHARBOR_API_KEY"],
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    },
    method="POST",
)
try:
    resp = urllib.request.urlopen(req, timeout=60)
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code} — {e.read().decode('utf-8', errors='replace')[:600]}")
    raise SystemExit(1)

frames = 0
had_done = False
usage_final = None
finish = None
first_lines = []
with resp:
    for line in resp:
        s = line.decode("utf-8", errors="replace").strip()
        if not s:
            continue
        if frames < 3:
            first_lines.append(s[:120])
        if s.startswith("data: "):
            payload = s[6:]
            if payload.strip() == "[DONE]":
                had_done = True
                continue
            frames += 1
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage_final = sorted(chunk["usage"].keys())
            ch = chunk.get("choices", [{}])
            if ch and ch[0].get("finish_reason"):
                finish = ch[0]["finish_reason"]

print(f"  data frames: {frames}   [DONE] sentinel: {had_done}   (SSE grammar, checklist #4)")
print(f"  finish_reason seen: {finish!r}")
print(f"  usage-bearing final chunk: {usage_final if usage_final else '(NONE — chars÷4 fallback stays)'}")
print(f"  first frames: {first_lines}")
PYEOF
echo
echo -e "${BOLD}Done.${NC} Feed the answers back into docs/api/TOKENHARBOR_API_TECHNICAL_REFERENCE.md"
echo -e "§16 probe checklist (flip the ⚠️ UNVERIFIED markers) before promoting out of Limited Support."
