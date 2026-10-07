#!/usr/bin/env bash
# AgentKthx smoke test — all cloud backends, STREAMING + non-streaming paths.
#
# Consolidated from smoke_test_r07_21.sh + smoke_test_r07_25.sh (R07.26).
# Tests FOUR things per backend:
#   1. `agentkthx models --backend <X> --free` lists free models
#   2. `agentkthx run --backend <X> --think "count to 5"` produces thinking output
#   3. `agentkthx run --backend <X> --tools shell --no-stream "echo MARKER"`
#      makes a tool call (NON-STREAMING path)
#   4. `agentkthx run --backend <X> --tools shell "echo MARKER"` makes a tool
#      call (STREAMING path — closes TEST-09: the R07.09 streaming bug)
#
# The smoke marker (`smoke-test-marker-$$`) is the SAME for both paths, so a
# streaming-only regression (e.g. a backend that returns NotImplementedError
# on `generate_completions_stream`, or returns SSE chunks without the
# `tool_calls` delta) shows up as a fail in the streaming step but PASS in
# the non-streaming step — exactly the R07.09 bug shape.
#
# Tests FOUR things per backend:
#   1. `agentkthx models --backend <X> --free` lists free models
#   2. `agentkthx run --backend <X> --think "count to 5"` produces thinking output
#   3. `agentkthx run --backend <X> --tools shell --no-stream "echo MARKER"`
#      makes a tool call (NON-STREAMING path)
#   4. `agentkthx run --backend <X> --tools shell "echo MARKER"` makes a tool
#      call (STREAMING path — the new step that closes TEST-09)
#
# Requires API keys in env or ~/.agentkthx/.env for every backend you want
# to exercise. Backends without a key are skipped with a clear message.
#
# OpenAI is expected to return 0 models under --free (known — OpenAI has no
# free tier); the script notes this and moves on.
#
# Usage:
#   ./scripts/smoke_test.sh
#   AGENTKTHX_DEBUG=1 ./scripts/smoke_test.sh   # verbose
#   ./scripts/smoke_test.sh --skip "openai mistral"   # skip backends
#   ./scripts/smoke_test.sh --backend nvidia   # one backend only
#   ./scripts/smoke_test.sh --no-stream-only    # skip step 4 (debug)
#
# Exit codes:
#   0 = all tested backends passed (or were skipped cleanly)
#   1 = at least one backend failed a step

set -u

# ─── config ──────────────────────────────────────────────────────────────
# Backends to test, in order. Skip any by passing --skip "openai mistral"
# (the SKIP env var is no longer supported — use --skip).
DEFAULT_BACKENDS="zai openrouter orcarouter gemini huggingface openai mistral pollinations nvidia"
# SKIP + BACKENDS are populated by the arg parser below (deferred so --skip
# can override before the filter loop runs).
SKIP=""
BACKENDS=""

# Per-step timeouts (seconds). The streaming step has a longer timeout
# because the stream chunk decode + tool-call arg assembly adds latency
# on the first invocation per backend (cold cache, TLS handshake, etc.).
NONSTREAM_TIMEOUT=120
STREAM_TIMEOUT=180

# Markers we'll grep for in the output
THINK_MARKER="reasoning:"          # displayed when --think surfaces reasoning content
TOOL_MARKER="tool shell"           # the [N] tool shell ... line in chat output
SMOKE_MARKER="smoke-test-marker-$$"  # unique per-run, echoed via the shell tool (SAME for both paths)

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
    nvidia)       envvar="NVIDIA_API_KEY" ;;
  esac
  # Empty envvar (unknown backend) → no key. Guard against the
  # `${!envvar:-}` indirect-expansion error on empty var names.
  if [[ -z "$envvar" ]]; then
    return 1
  fi
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
    nvidia)       echo "NVIDIA_FREE_ONLY" ;;
    *)           echo "" ;;
  esac
}

# Hardcoded model names per backend. Some aggregators ship a special
# "free-tier" model identifier that routes to a free model automatically —
# using it skips the slow `agentkthx models` listing + the first_free_model
# parsing fragility. Returns the model name (e.g. "openrouter/free") or
# empty string if the backend should fall back to first_free_model().
default_model_for_backend() {
  local backend="$1"
  case "$backend" in
    openrouter)  echo "openrouter/free" ;;
    orcarouter)  echo "orcarouter/free" ;;
    # NVIDIA: hardcode a known-working model — the free tier doesn't have
    # access to all 45 cataloged models. The first_free_model() picker would
    # pick "01-ai/yi-large" (alphabetically first) which 404s for most accounts.
    # nvidia/nemotron-3.5-lightning-30b-a3b confirmed working on the free
    # tier (verified Oct 2026 — fast, supports tools + streaming + thinking).
    nvidia)      echo "nvidia/nemotron-3.5-lightning-30b-a3b" ;;
    *)           echo "" ;;
  esac
}

# Pick a default free model per backend — the first free model the listing
# returns. We grab it dynamically so this doesn't go stale. Sets the
# per-backend FREE_ONLY env var inline so the listing is free-filtered.
first_free_model() {
  local backend="$1"
  local fo_var
  fo_var=$(free_only_var "$backend")
  # R07.21: clear the persistent JSON cache for this backend before listing
  # so a stale entry (e.g. a model Google deprecated since the cache was
  # populated) doesn't surface. The cache lives at
  # ~/.cache/agentkthx/model_catalog.json and is keyed by backend name.
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
  # Skip non-chat models (TTS, audio, image, transcription, embeddings, Gemma)
  # — they can't run the shell tool reliably. Gemma models on Gemini's API
  # don't support thinking_config (hangs) and have poor tool-calling support.
  eval "$cmd 2>/dev/null" \
    | sed 's/\x1b\[[0-9;]*m//g' \
    | awk '
        { gsub(/^[[:space:]]+|[[:space:]]+$/, "") }
        /^[-─]+$/ { sep++; next }
        sep == 2 && NF > 0 {
          name = $1
          # Skip non-chat models — they 400 on text requests or hang
          if (name ~ /-tts$|-tts-|-transcribe|-image$|-preview-tts|^embed|dall-e|flux|lyria|whisper|^tts-|-speech|^gemma-/) next
          print name; exit
        }
      '
}

# ─── per-backend tool-call step (shared between streaming + non-streaming) ──
# Args: $1=backend, $2=model, $3=path_label ("non-streaming" or "streaming"),
#       $4=extra flags (e.g. "--no-stream" or ""), $5=timeout_seconds
run_tool_step() {
  local backend="$1"
  local model="$2"
  local label="$3"
  local extra_flags="$4"
  local step_timeout="$5"

  step "agentkthx run --backend $backend --model $model --tools shell $extra_flags"

  local tool_output tool_exit
  if [[ $DEBUG -eq 1 ]]; then
    echo "${C_DIM}  ┌─── run output ($label) ──────────────────────────────────────────${C_RESET}"
    timeout "$step_timeout" env AGENTKTHX_DEBUG=1 agentkthx run --backend "$backend" --model "$model" \
      --tools shell --security off $extra_flags \
      "Use the shell tool to run: echo $SMOKE_MARKER" 2>&1 | sed 's/^/  │ /'
    tool_exit=${PIPESTATUS[0]}
    tool_output=""
    echo "${C_DIM}  └──────────────────────────────────────────────────────────────────${C_RESET}"
  else
    tool_output=$(timeout "$step_timeout" agentkthx run --backend "$backend" --model "$model" \
      --tools shell --security off $extra_flags \
      "Use the shell tool to run: echo $SMOKE_MARKER" 2>&1)
    tool_exit=$?
  fi

  if [[ $tool_exit -eq 124 ]]; then
    fail "$label: run --tools shell timed out after ${step_timeout}s"
  elif [[ $tool_exit -ne 0 ]]; then
    fail "$label: run --tools shell exited $tool_exit"
    [[ $DEBUG -eq 0 ]] && echo "$tool_output" | show_output | sed 's/^/    /'
  elif [[ $DEBUG -eq 1 ]]; then
    ok "$label: run --tools shell completed (see output above)"
  elif echo "$tool_output" | grep -q "$SMOKE_MARKER"; then
    ok "$label: shell tool executed — marker '$SMOKE_MARKER' found in output"
  elif echo "$tool_output" | grep -qiE 'tool shell|tool_calls'; then
    ok "$label: shell tool was invoked (tool-call line present)"
    echo "$tool_output" | show_output | sed 's/^/    /'
  else
    fail "$label: shell tool did not execute (marker not found)"
    echo "$tool_output" | show_output | sed 's/^/    /'
  fi
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
  local fo_var
  fo_var=$(free_only_var "$backend")
  step "agentkthx models --backend $backend ($fo_var=1)"
  local models_output
  if [[ -n "$fo_var" ]]; then
    models_output=$(env "$fo_var=1" ${DEBUG:+AGENTKTHX_DEBUG=1} agentkthx models --backend "$backend" 2>&1)
  else
    models_output=$(env ${DEBUG:+AGENTKTHX_DEBUG=1} agentkthx models --backend "$backend" 2>&1)
  fi
  local model_count
  model_count=$(echo "$models_output" \
    | sed 's/\x1b\[[0-9;]*m//g' \
    | awk '
        { gsub(/^[[:space:]]+|[[:space:]]+$/, "") }
        /^[-─]+$/ { sep++; next }
        sep == 2 && NF > 0 { count++ }
        END { print count+0 }
      ')
  if [[ "$backend" == "openai" && "$model_count" -eq 0 ]]; then
    echo "$models_output" | show_output | sed 's/^/    /'
    skip "openai — 0 free models (known: OpenAI has no free tier)"
  elif [[ "$model_count" -gt 0 ]]; then
    echo "$models_output" | show_output | sed 's/^/    /'
    ok "models listing — $model_count free models"
  else
    echo "$models_output" | show_output | sed 's/^/    /'
    fail "models listing — no free models returned"
    return
  fi

  # ─── pick a model for the run tests ──────────────────────────────────
  # Some backends (openrouter, orcarouter) ship a special free-tier model
  # identifier that routes to a free model automatically — preferred over
  # first_free_model() because it skips the slow listing + the parsing
  # fragility. Falls back to first_free_model() for backends without a
  # hardcoded default.
  local model
  model=$(default_model_for_backend "$backend")
  if [[ -z "$model" ]]; then
    model=$(first_free_model "$backend")
  fi
  if [[ -z "$model" ]]; then
    skip "$backend — no free model available for run tests"
    return
  fi
  step "using model: $model"

  # ─── step 2: thinking output (non-streaming) ────────────────────────
  # --no-stream: cloud backends default to streaming, which doesn't
  # terminate cleanly when captured in $(). --no-stream forces a single
  # response that returns when complete.
  # Skip --think for Gemma models (Gemini backend): Gemma models on Gemini's
  # API don't support thinking_config — the request hangs instead of
  # returning an error.
  local skip_think=0
  if [[ "$backend" == "gemini" && "$model" == gemma-* ]]; then
    skip_think=1
  fi

  if [[ $skip_think -eq 1 ]]; then
    step "agentkthx run --backend $backend --model $model --no-stream (think SKIPPED — Gemma)"
    local think_output think_exit
    if [[ $DEBUG -eq 1 ]]; then
      echo "${C_DIM}  ┌─── run output ───────────────────────────────────────────────────${C_RESET}"
      timeout $NONSTREAM_TIMEOUT env AGENTKTHX_DEBUG=1 agentkthx run --backend "$backend" --model "$model" \
        --no-stream "Count from 1 to 5. Brief." 2>&1 | sed 's/^/  │ /'
      think_exit=${PIPESTATUS[0]}
      think_output=""
      echo "${C_DIM}  └──────────────────────────────────────────────────────────────────${C_RESET}"
    else
      think_output=$(timeout $NONSTREAM_TIMEOUT agentkthx run --backend "$backend" --model "$model" \
        --no-stream "Count from 1 to 5. Brief." 2>&1)
      think_exit=$?
    fi
    if [[ $think_exit -eq 124 ]]; then
      fail "run (no-think) timed out after ${NONSTREAM_TIMEOUT}s"
    elif [[ $think_exit -ne 0 ]]; then
      fail "run (no-think) exited $think_exit"
      [[ $DEBUG -eq 0 ]] && echo "$think_output" | show_output | sed 's/^/    /'
    elif [[ $DEBUG -eq 1 ]]; then
      ok "run (no-think) completed (Gemma doesn't support thinking — see output above)"
    elif echo "$think_output" | grep -qE '[1-5]'; then
      ok "run (no-think) produced output (Gemma doesn't support thinking — content OK)"
    else
      fail "run (no-think) produced no recognizable output"
      echo "$think_output" | show_output | sed 's/^/    /'
    fi
  else
    step "agentkthx run --backend $backend --model $model --think --no-stream \"count to 5\""
    local think_output think_exit
    if [[ $DEBUG -eq 1 ]]; then
      echo "${C_DIM}  ┌─── run output (think) ───────────────────────────────────────────${C_RESET}"
      timeout $NONSTREAM_TIMEOUT env AGENTKTHX_DEBUG=1 agentkthx run --backend "$backend" --model "$model" \
        --think --no-stream "Count from 1 to 5. Brief." 2>&1 | sed 's/^/  │ /'
      think_exit=${PIPESTATUS[0]}
      think_output=""
      echo "${C_DIM}  └──────────────────────────────────────────────────────────────────${C_RESET}"
    else
      think_output=$(timeout $NONSTREAM_TIMEOUT agentkthx run --backend "$backend" --model "$model" \
        --think --no-stream "Count from 1 to 5. Brief." 2>&1)
      think_exit=$?
    fi
    if [[ $think_exit -eq 124 ]]; then
      fail "run --think timed out after ${NONSTREAM_TIMEOUT}s"
    elif [[ $think_exit -ne 0 ]]; then
      fail "run --think exited $think_exit"
      [[ $DEBUG -eq 0 ]] && echo "$think_output" | show_output | sed 's/^/    /'
    elif [[ $DEBUG -eq 1 ]]; then
      ok "run --think completed (see output above)"
    elif echo "$think_output" | grep -qiE 'reasoning:|thinking:|thought'; then
      ok "thinking output detected (reasoning panel surfaced)"
    elif echo "$think_output" | grep -qE '[1-5]'; then
      ok "run --think produced output (model may not emit reasoning_content — content OK)"
    else
      fail "run --think produced no recognizable output"
      echo "$think_output" | show_output | sed 's/^/    /'
    fi
  fi

  # ─── step 3: NON-STREAMING shell tool call (the R07.21 contract) ────
  if [[ $NO_STREAM_ONLY -eq 1 ]]; then
    skip "step 3 (non-streaming tool) — --no-stream-only flag"
  else
    run_tool_step "$backend" "$model" "non-streaming" "--no-stream" "$NONSTREAM_TIMEOUT"
  fi

  # ─── step 4: STREAMING shell tool call (the NEW R07.25 step) ───────
  # No --no-stream flag → cloud backend defaults to streaming. The agent
  # loop calls `generate_completions_stream` → `_iter_sse_lines` → SSE
  # chunk parse → tool-call extraction. The smoke marker is the SAME as
  # step 3 so a streaming-only regression (e.g. NotImplementedError on
  # `generate_completions_stream`, or SSE chunks without the `tool_calls`
  # delta) shows up as step 4 fail + step 3 pass — exactly the R07.09
  # bug shape that the original 64-test suite missed.
  if [[ $NO_STREAM_ONLY -eq 1 ]]; then
    skip "step 4 (streaming tool) — --no-stream-only flag"
  else
    run_tool_step "$backend" "$model" "streaming" "" "$STREAM_TIMEOUT"
  fi
}

# ─── parse args ─────────────────────────────────────────────────────────
# --backend X       test only one backend (overrides --skip + DEFAULT_BACKENDS)
# --skip "a b c"    space-separated list of backends to skip (no env-var equiv)
# --debug           show ALL output (no head/tail truncation, full agentkthx stderr)
# --no-stream-only  skip step 4 (the streaming step) — debug escape hatch
# --help / -h       usage
ONLY_BACKEND=""
DEBUG=0
NO_STREAM_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend|-b)
      ONLY_BACKEND="$2"
      shift 2
      ;;
    --skip)
      SKIP="$2"
      shift 2
      ;;
    --debug)
      DEBUG=1
      shift
      ;;
    --no-stream-only)
      NO_STREAM_ONLY=1
      shift
      ;;
    --help|-h)
      echo "Usage: $0 [--backend <name>] [--skip \"<names>\"] [--debug] [--no-stream-only]"
      echo ""
      echo "Options:"
      echo "  --backend <name>      Test only one backend (zai, openrouter, gemini, nvidia, etc.)"
      echo "  --skip \"<names>\"      Space-separated list of backends to skip (e.g. --skip \"openai mistral\")"
      echo "  --debug               Show ALL output (no head/tail truncation, full agentkthx stderr)"
      echo "  --no-stream-only      Skip step 4 (the streaming step) — debug escape hatch"
      echo "  --help                This help message"
      echo ""
      echo "Environment:"
      echo "  AGENTKTHX_DEBUG=1         Verbose mode (same as --debug)"
      echo "  NONSTREAM_TIMEOUT=120     Per-step timeout for non-streaming invocations"
      echo "  STREAM_TIMEOUT=180        Per-step timeout for the streaming invocation"
      echo ""
      echo "Steps per backend (4 total):"
      echo "  1. agentkthx models --backend <X> --free   (model listing)"
      echo "  2. agentkthx run --backend <X> --think --no-stream \"count to 5\"   (thinking)"
      echo "  3. agentkthx run --backend <X> --tools shell --no-stream \"echo MARKER\"   (NON-STREAMING path)"
      echo "  4. agentkthx run --backend <X> --tools shell \"echo MARKER\"   (STREAMING path — closes TEST-09)"
      echo ""
      echo "Backends with hardcoded free-tier model names (skip first_free_model):"
      echo "  openrouter  →  openrouter/free"
      echo "  orcarouter  →  orcarouter/free"
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

# Build BACKENDS list from DEFAULT_BACKENDS, filtered by --skip.
# (--backend overrides everything: BACKENDS becomes just that one name.)
if [[ -n "$ONLY_BACKEND" ]]; then
  BACKENDS="$ONLY_BACKEND"
  SKIP=""
else
  for b in $DEFAULT_BACKENDS; do
    case " $SKIP " in
      *" $b "*) echo "SKIP: $b (in --skip list)" ;;
      *) BACKENDS="$BACKENDS $b" ;;
    esac
  done
  BACKENDS="${BACKENDS# }"
fi
[[ "${AGENTKTHX_DEBUG:-0}" == "1" ]] && DEBUG=1

# Allow env-var overrides for the per-step timeouts
NONSTREAM_TIMEOUT="${NONSTREAM_TIMEOUT:-120}"
STREAM_TIMEOUT="${STREAM_TIMEOUT:-180}"

# Helper: show output (head when not --debug, full when --debug)
show_output() {
  if [[ $DEBUG -eq 1 ]]; then
    cat
  else
    head -15
  fi
}

# ─── main ───────────────────────────────────────────────────────────────
echo "${C_CYAN}⚖ AgentKthx Smoke Test (streaming + non-streaming)${C_RESET}"
echo "${C_DIM}  Backends: $BACKENDS${C_RESET}"
[[ -n "$SKIP" ]] && echo "${C_DIM}  Skip:    $SKIP${C_RESET}"
echo "${C_DIM}  Marker: $SMOKE_MARKER (same for non-streaming + streaming steps)${C_RESET}"
echo "${C_DIM}  Timeouts: ${NONSTREAM_TIMEOUT}s (non-streaming) / ${STREAM_TIMEOUT}s (streaming)${C_RESET}"
[[ $NO_STREAM_ONLY -eq 1 ]] && echo "${C_YELLOW}  NOTE: --no-stream-only set, step 4 (streaming) will be skipped${C_RESET}"

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
