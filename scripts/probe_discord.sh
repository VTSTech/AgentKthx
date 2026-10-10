#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# probe_discord.sh — Discord plugin smoke probe (M1)
# ═══════════════════════════════════════════════════════════════════════════
# Five numbered checks; prints PASS/FAIL per check and exits non-zero if
# any required check fails. Safe to run repeatedly (check 4 makes a short
# real gateway connection, everything else is offline or read-only).
#
#   1. Plugin discovery    — `discord` listed as loaded feature plugin
#   2. Token self-check    — DISCORD_BOT_TOKEN resolves via GET /users/@me
#   3. Policy unit smoke   — cooldown + allowlist deny-by-default behave
#   4. Gateway dry-run     — real connect/identify/READY, 20s soak, clean exit
#   5. Session store       — `agentkthx sessions` reachable; discord- rows visible
#
# Usage:    bash scripts/probe_discord.sh
# Token:    DISCORD_BOT_TOKEN env or ~/.agentkthx/.env (from `discord setup`)
# ═══════════════════════════════════════════════════════════════════════════
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
REPO_ROOT="$PWD"
ENV_FILE="${HOME}/.agentkthx/.env"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
PASS=0; FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} — $1"; PASS=$((PASS+1)); }
fail() { echo -e "  ${RED}FAIL${NC} — $1"; FAIL=$((FAIL+1)); }
info() { echo -e "  ${CYAN}···${NC} $1"; }

# Load the setup wizard's env file (never prints values)
if [ -f "$ENV_FILE" ]; then
  while IFS='=' read -r k v; do
    case "$k" in \#*|"") continue ;; esac
    k="${k#export }"
    case "$k" in
      DISCORD_*) [ -z "${!k+x}" ] && export "$k=$v" ;;
    esac
  done < <(grep -v '^\s*$' "$ENV_FILE")
  info "loaded DISCORD_* settings from $ENV_FILE (env vars win)"
fi

echo -e "${CYAN}═══ AgentKthx Discord Probe ═══${NC}"
echo ""

# ── 1. Plugin discovery ────────────────────────────────────────────────────
echo -e "${CYAN}── 1. Plugin discovery ──${NC}"
PLUGINS_OUT="$(python3 -c 'from agentkthx.cli import main; main()' plugins 2>/dev/null || true)"
if echo "$PLUGINS_OUT" | grep -q "discord"; then
  pass "discord plugin listed by 'agentkthx plugins'"
else
  fail "discord plugin missing from 'agentkthx plugins' output"
fi
echo ""

# ── 2. Token self-check ────────────────────────────────────────────────────
echo -e "${CYAN}── 2. Token self-check (GET /users/@me) ──${NC}"
if [ -z "${DISCORD_BOT_TOKEN:-}" ]; then
  fail "DISCORD_BOT_TOKEN not set (env or $ENV_FILE) — run: agentkthx discord setup"
else
  ME_OUT="$(python3 - "$DISCORD_BOT_TOKEN" <<'PY'
import sys
sys.path.insert(0, ".")
from agentkthx.plugins.discord.rest import DiscordRest, DiscordRestError
try:
    me = DiscordRest(sys.argv[1], timeout=10).get_self()
    print(f"OK {me.get('username')} ({me.get('id')})")
except DiscordRestError as e:
    print(f"ERR {e}")
except Exception as e:
    print(f"ERR {type(e).__name__}: {e}")
PY
)"
  case "$ME_OUT" in
    OK*) info "$ME_OUT"; pass "token valid" ;;
    ERR*) fail "token self-check: $ME_OUT" ;;
    *) fail "token self-check produced no result" ;;
  esac
fi
echo ""

# ── 3. Policy unit smoke (offline) ─────────────────────────────────────────
echo -e "${CYAN}── 3. Policy unit smoke (deny-by-default + cooldown) ──${NC}"
POLICY_OUT="$(python3 - <<'PY'
import sys
sys.path.insert(0, ".")
from agentkthx.plugins.discord.policy import Policy, MessageContext
p = Policy(bot_user_id="B", allow_guilds=["G"], allow_users=["U"], cooldown_s=10)
ev_ok = MessageContext("m1", "C", "G", "U", "u", f"<@B> hi", mentions=("B",))
ev_foreign = MessageContext("m2", "C", "X", "U", "u", f"<@B> hi", mentions=("B",))
checks = [
    p.check_event(ev_ok).allowed,
    not p.check_event(ev_foreign).allowed,          # unlisted guild denied
    p.check_rate("U").allowed,                      # first run passes
    not p.check_rate("U").allowed,                  # second held by cooldown
]
print("OK" if all(checks) else "FAIL")
PY
)"
if [ "$POLICY_OUT" = "OK" ]; then
  pass "allowlist gate + trigger + cooldown bucket behave"
else
  fail "policy smoke failed ($POLICY_OUT)"
fi
echo ""

# ── 4. Gateway dry-run soak ────────────────────────────────────────────────
echo -e "${CYAN}── 4. Gateway dry-run (20s soak: connect → identify → READY) ──${NC}"
if [ -z "${DISCORD_BOT_TOKEN:-}" ]; then
  fail "skipped — no token"
else
  SOAK_OUT="$(timeout 20 python3 -c '
import sys, threading, time
sys.path.insert(0, ".")
from agentkthx.plugins.discord.gateway import GatewayClient
ready = threading.Event()
def on_dispatch(t, d):
    if t in ("READY", "RESUMED"):
        ready.set()
c = GatewayClient(sys.argv[1], 37377, on_dispatch, log=lambda m: None)
def runner():
    try: c.run_forever()
    except Exception: pass
th = threading.Thread(target=runner, daemon=True)
th.start()
ok = ready.wait(15)
c.stop()
print("READY" if ok else "NO-READY")
' "$DISCORD_BOT_TOKEN" 2>/dev/null || true)"
  if echo "$SOAK_OUT" | grep -q "READY"; then
    pass "gateway connected, identified and reached READY"
  else
    fail "gateway did not reach READY in 15s ($SOAK_OUT)"
  fi
fi
echo ""

# ── 5. Session store ───────────────────────────────────────────────────────
echo -e "${CYAN}── 5. Session store reachability ──${NC}"
SESS_OUT="$(python3 -c '
import sys
sys.path.insert(0, ".")
from agentkthx.core.persistent_memory import PersistentMemory
rows = PersistentMemory.list_sessions()
disc = [r["session_id"] for r in rows if r["session_id"].startswith("discord-")]
print(f"OK total={len(rows)} discord={len(disc)}")
' 2>/dev/null || echo "ERR")"
case "$SESS_OUT" in
  OK*) info "$SESS_OUT"; pass "PersistentMemory store readable (discord- rows: ${SESS_OUT#*discord=})" ;;
  *) fail "could not read session store" ;;
esac
echo ""

echo -e "${CYAN}═══ Result: ${PASS} passed, ${FAIL} failed ═══${NC}"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
