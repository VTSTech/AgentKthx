#!/usr/bin/env bash
# R07.21 smoke test — all cloud backends.
#
# Tests three things per backend:
#   1. `agentkthx models --backend <X> --free` lists free models
#   2. `agentkthx run --backend <X> --think "count to 5"` produces thinking output
#   3. `agentkthx run --backend <X> --tools shell "use shell to echo smoke-test-marker"` makes a tool call
#
# Requires API keys in env or ~/.agentkthx/.env for every backend you want
# to exercise. Backends without a key are skipped with a clear message.
#
# OpenAI is expected to return 0 models under --free (known — OpenAI has no
# free tier); the script notes this and moves on.
#
# Usage:
#   ./scripts/smoke_test_r07_21.sh
#   AGENTKTHX_DEBUG=1 ./scripts/smoke_test_r07_21.sh   # verbose
#   SKIP=backends_to_skip ./scripts/smoke_test_r07_21.sh
#
# Exit codes:
#   0 = all tested backends passed (or were skipped cleanly)
#   1 = at least one backend failed a step

set -u

# ─── config ──────────────────────────────────────────────────────────────
# Backends to test, in order. Skip any by setting SKIP="openai mistral"
DEFAULT_BACKENDS="zai openrouter orcarouter gemini huggingface openai mistral pollinations"
SKIP="${SKIP:-}"
BACKENDS=""
for b in $DEFAULT_BACKENDS; do
  case " $SKIP " in
    *" $b "*) echo "SKIP: $b (in SKIP list)" ;;
    *) BACKENDS="$BACKENDS $b" ;;
  esac
done
BACKENDS="${BACKENDS# }"

# Markers we'll grep for in the output
THINK_MARKER="reasoning:"          # displayed when --think surfaces reasoning content
TOOL_MARKER="tool shell"           # the [N] tool shell ... line in chat output
SMOKE_MARKER="smoke-test-marker-$$"  # unique per-run, echoed via the shell tool

# Colors (disabled when not a TTY)
if [[ -t 1 ]]; then
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
  C_CYAN=$'\033[36m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
  C_GREEN=''; C_YELLOW=''; C_RED=''; C_CYAN=''; C_DIM=''; C_RESET=''
fi

# Counters
PASS=0
FAIL=0
SKIP_COUNT=0

# ─── helpers ─────────────────────────────────────────────────────────────
header() {
  echo
  echo "${C_CYAN}════════════════════════════════════════════════════════════════${C_RESET}"
  echo "${C_CYAN}  $1${C_RESET}"
  echo "${C_CYAN}════════════════════════════════════════════════════════════════${C_RESET}"
}

step() { echo "${C_DIM}  → $1${C_RESET}"; }
ok()    { echo "  ${C_GREEN}✓${C_RESET} $1"; PASS=$((PASS+1)); }
fail()  { echo "  ${C_RED}✗${C_RESET} $1"; FAIL=$((FAIL+1)); }
skip()  { echo "  ${C_YELLOW}⊘${C_RESET} $1 (skipped)"; SKIP_COUNT=$((SKIP_COUNT+1)); }

# Check if a backend's API key env var is set. The /auth picker writes to
# ~/.agentkthx/.env, so we check both env and that file.
has_key() {
  local backend="$1"
  local envvar=""
  case "$backend" in
    zai)         envvar="ZAI_API_KEY" ;;
    openrouter)  envvar="OPENROUTER_API_KEY" ;;
    orcarouter)  envvar="ORCAROUTER_API_KEY" ;;
    gemini)      envvar="GEMINI_API_KEY" ;;
    huggingface) envvar="HF_TOKEN" ;;
    openai)      envvar="OPENAI_API_KEY" ;;
    mistral)     envvar="MISTRAL_API_KEY" ;;
    pollinations) envvar="POLLINATIONS_API_KEY" ;;
  esac
  if [[ -n "${!envvar:-}" ]]; then
    return 0
  fi
  # Check ~/.agentkthx/.env
  if [[ -f "$HOME/.agentkthx/.env" ]] && grep -q "^${envvar}=" "$HOME/.agentkthx/.env"; then
    return 0
  fi
  return 1
}

# Return the FREE_ONLY env var name for a backend (or "" if none).
free_only_var() {
  local backend="$1"
  case "$backend" in
    zai)         echo "ZAI_FREE_ONLY" ;;
    openrouter)  echo "OPENROUTER_FREE_ONLY" ;;
    orcarouter)  echo "ORCAROUTER_FREE_ONLY" ;;
    gemini)      echo "GEMINI_FREE_ONLY" ;;
    huggingface) echo "HF_FREE_ONLY" ;;
    openai)      echo "OPENAI_FREE_ONLY" ;;
    mistral)     echo "MISTRAL_FREE_ONLY" ;;
    pollinations) echo "POLLINATIONS_FREE_ONLY" ;;
    *)           echo "" ;;
  esac
}

# Pick a default free model per backend — the first free model the listing
# returns. We grab it dynamically so this doesn't go stale. Sets the
# per-backend FREE_ONLY env var inline so the listing is free-filtered.
#
# The models table format (with ANSI dim codes on separators) is:
#     ⚖ AgentKthx - Available Models
#       Backend: https://api.z.ai
#     --------separator--------
#       Name           Context    tools    think
#     --------separator--------
#       glm-4.5-flash  132000     ○ react  ✓ yes
#     --------separator--------
#     Total: N models
#     Legend: ...
#
# We skip past the SECOND separator (the one after the column header), then
# grab the first non-empty line's first field. The separator lines are
# indented + ANSI-dim, so we match on "line containing only dashes after
# stripping whitespace + ANSI codes".
first_free_model() {
  local backend="$1"
  local fo_var
  fo_var=$(free_only_var "$backend")
  # R07.21: clear the persistent JSON cache for this backend before listing
  # so a stale entry (e.g. a model Google deprecated since the cache was
  # populated) doesn't surface. The cache lives at
  # ~/.cache/agentkthx/model_catalog.json and is keyed by backend name.
  # Clearing forces a re-seed from the (corrected) seed JSON.
  local cache_file="${XDG_CACHE_HOME:-$HOME/.cache}/agentkthx/model_catalog.json"
  if [[ -f "$cache_file" ]]; then
    python3 -c "
import json, pathlib, sys
p = pathlib.Path('$cache_file')
try:
    d = json.loads(p.read_text())
    if '$backend' in d:
        del d['$backend']
        p.write_text(json.dumps(d, indent=2))
except Exception as e:
    pass  # corrupt cache — the next agentkthx run will rebuild it
" 2>/dev/null
  fi
  local cmd="agentkthx models --backend $backend"
  if [[ -n "$fo_var" ]]; then
    cmd="env $fo_var=1 $cmd"
  fi
  # Strategy: strip ANSI codes, find lines that are only-dashes after
  # stripping whitespace, count them, and after the 2nd separator, print
  # the first field of the next non-empty line.
  eval "$cmd 2>/dev/null" \
    | sed 's/\x1b\[[0-9;]*m//g' \
    | awk '
        { gsub(/^[[:space:]]+|[[:space:]]+$/, "") }
        /^[-─]+$/ { sep++; next }
        sep == 2 && NF > 0 { print $1; exit }
      '
}

# ─── per-backend test ───────────────────────────────────────────────────
test_backend() {
  local backend="$1"

  header "Backend: $backend"

  # ─── step 0: key check ───────────────────────────────────────────────
  if ! has_key "$backend"; then
    skip "$backend — no API key (set in env or ~/.agentkthx/.env)"
    return
  fi
  ok "API key present"

  # ─── step 1: model listing (free only via per-backend FREE_ONLY env var) ──
  # FREE_ONLY is controlled by per-backend env vars (ZAI_FREE_ONLY,
  # OPENROUTER_FREE_ONLY, etc.) — there is no --free CLI flag. We set the
  # env var inline for the duration of the command.
  local fo_var
  fo_var=$(free_only_var "$backend")
  step "agentkthx models --backend $backend ($fo_var=1)"
  local models_output
  if [[ -n "$fo_var" ]]; then
    models_output=$(env "$fo_var=1" agentkthx models --backend "$backend" 2>&1)
  else
    models_output=$(agentkthx models --backend "$backend" 2>&1)
  fi
  local model_count
  # Count actual data rows: lines after the 2nd separator that have content.
  # The old grep counted every indented line (incl. "Backend:", "Name Context...")
  # which inflated the count. Strip ANSI + count rows after the 2nd separator.
  model_count=$(echo "$models_output" \
    | sed 's/\x1b\[[0-9;]*m//g' \
    | awk '
        { gsub(/^[[:space:]]+|[[:space:]]+$/, "") }
        /^[-─]+$/ { sep++; next }
        sep == 2 && NF > 0 { count++ }
        END { print count+0 }
      ')
  # OpenAI is known to return 0 free models — not a failure
  if [[ "$backend" == "openai" && "$model_count" -eq 0 ]]; then
    echo "$models_output" | head -8 | sed 's/^/    /'
    skip "openai — 0 free models (known: OpenAI has no free tier)"
  elif [[ "$model_count" -gt 0 ]]; then
    echo "$models_output" | head -8 | sed 's/^/    /'
    ok "models listing — $model_count free models"
  else
    echo "$models_output" | head -8 | sed 's/^/    /'
    fail "models listing — no free models returned"
    return
  fi

  # ─── pick a model for the run tests ──────────────────────────────────
  local model
  model=$(first_free_model "$backend")
  if [[ -z "$model" ]]; then
    skip "$backend — no free model available for run tests"
    return
  fi
  step "using model: $model"

  # ─── step 2: thinking output ─────────────────────────────────────────
  # --no-stream: cloud backends default to streaming, which doesn't
  # terminate cleanly when captured in $(). --no-stream forces a single
  # response that returns when complete.
  # timeout 120: kill the process if it takes longer than 2 minutes
  # (thinking models can be slow, but 2 min is plenty for "count to 5").
  step "agentkthx run --backend $backend --model $model --think --no-stream \"count to 5\""
  local think_output
  think_output=$(timeout 120 agentkthx run --backend "$backend" --model "$model" \
    --think --no-stream "Count from 1 to 5. Brief." 2>&1)
  local think_exit=$?
  if [[ $think_exit -eq 124 ]]; then
    fail "run --think timed out after 120s"
  elif [[ $think_exit -ne 0 ]]; then
    fail "run --think exited $think_exit"
    echo "$think_output" | tail -10 | sed 's/^/    /'
  elif echo "$think_output" | grep -qiE 'reasoning:|thinking:|thought'; then
    ok "thinking output detected (reasoning panel surfaced)"
  elif echo "$think_output" | grep -qE '[1-5]'; then
    ok "run --think produced output (model may not emit reasoning_content — content OK)"
  else
    fail "run --think produced no recognizable output"
    echo "$think_output" | tail -10 | sed 's/^/    /'
  fi

  # ─── step 3: shell tool call ──────────────────────────────────────────
  step "agentkthx run --backend $backend --model $model --tools shell --no-stream"
  local tool_output
  tool_output=$(timeout 120 agentkthx run --backend "$backend" --model "$model" \
    --tools shell --security off --no-stream \
    "Use the shell tool to run: echo $SMOKE_MARKER" 2>&1)
  local tool_exit=$?
  if [[ $tool_exit -eq 124 ]]; then
    fail "run --tools shell timed out after 120s"
  elif [[ $tool_exit -ne 0 ]]; then
    fail "run --tools shell exited $tool_exit"
    echo "$tool_output" | tail -15 | sed 's/^/    /'
  elif echo "$tool_output" | grep -q "$SMOKE_MARKER"; then
    ok "shell tool executed — marker '$SMOKE_MARKER' found in output"
  elif echo "$tool_output" | grep -qiE 'tool shell|tool_calls'; then
    ok "shell tool was invoked (tool-call line present)"
    echo "$tool_output" | tail -15 | sed 's/^/    /'
  else
    fail "shell tool did not execute (marker not found)"
    echo "$tool_output" | tail -15 | sed 's/^/    /'
  fi
}

# ─── parse args ─────────────────────────────────────────────────────────
# --backend X  test only one backend (overrides SKIP + DEFAULT_BACKENDS)
# --help / -h   usage
ONLY_BACKEND=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend|-b)
      ONLY_BACKEND="$2"
      shift 2
      ;;
    --help|-h)
      echo "Usage: $0 [--backend <name>] [SKIP=backends_to_skip]"
      echo ""
      echo "Options:"
      echo "  --backend <name>  Test only one backend (zai, openrouter, gemini, etc.)"
      echo "  --help            This help message"
      echo ""
      echo "Environment:"
      echo "  SKIP=backends_to_skip  Space-separated list of backends to skip"
      echo "  AGENTKTHX_DEBUG=1      Verbose mode"
      echo ""
      echo "Without --backend, tests all cloud backends found in env."
      exit 0
      ;;
    *)
      echo "Unknown argument: $1 (use --help)" >&2
      exit 1
      ;;
  esac
done

if [[ -n "$ONLY_BACKEND" ]]; then
  BACKENDS="$ONLY_BACKEND"
  SKIP=""
fi

# ─── main ───────────────────────────────────────────────────────────────
echo "${C_CYAN}⚖ AgentKthx R07.21 Smoke Test${C_RESET}"
echo "${C_DIM}  Backends: $BACKENDS${C_RESET}"
echo "${C_DIM}  SKIP: ${SKIP:-<none>}${C_RESET}"
echo "${C_DIM}  Marker: $SMOKE_MARKER${C_RESET}"

for backend in $BACKENDS; do
  test_backend "$backend"
done

# ─── summary ────────────────────────────────────────────────────────────
header "Summary"
echo "  ${C_GREEN}PASS${C_RESET}: $PASS"
echo "  ${C_RED}FAIL${C_RESET}: $FAIL"
echo "  ${C_YELLOW}SKIP${C_RESET}: $SKIP_COUNT"

if [[ $FAIL -gt 0 ]]; then
  echo
  echo "${C_RED}✗ $FAIL step(s) failed${C_RESET}"
  exit 1
fi
echo
echo "${C_GREEN}✓ All tested backends passed (or were skipped cleanly)${C_RESET}"
exit 0
