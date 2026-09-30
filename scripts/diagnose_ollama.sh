#!/bin/bash
# ============================================================================
# diagnose_ollama.sh — Ollama Model Health Checker
#
# Usage:
#   ./diagnose_ollama.sh <model_name>               # Check a single model
#   ./diagnose_ollama.sh --all                      # Check all installed models
#   ./diagnose_ollama.sh --all --json               # All models, JSON output
#   ./diagnose_ollama.sh --all --max-size 4096      # Skip models > 4 GB
#   ./diagnose_ollama.sh <model> --force             # Bypass memory safety (DANGER)
#
# Examples:
#   ./diagnose_ollama.sh dlasher/granite-4.2-3b-GGUF:IQ4_NL
#   ./diagnose_ollama.sh qwen2.5:3b-instruct-q4_K_M
#   ./diagnose_ollama.sh krith/meta-llama-3.2-1b-instruct-uncensored:IQ4_XS
#   ./diagnose_ollama.sh --all
#
# Memory safety:
#   - Models larger than MAX_FILE_MB (default 8192 = 8 GB) are SKIPPED by default
#     to prevent OOM-killing the script. Override with --max-size <MB>.
#   - Pre-load check: if free_mem < file_size * 1.5, model is skipped.
#   - Use --force to bypass all safety checks (can OOM-kill the whole script).
#
# Detects:
#   1. fp16 fallback (file_size << peak_RSS) — the IQ4_NL silent expansion bug
#   2. Context override (model forces ctx != what you asked for)
#   3. GGUF metadata lying about quant type
#   4. Load failures / OOM kills
#   5. General health: tok/s, memory, load time
#
# Exit codes:
#   0 = all healthy (or single model healthy)
#   1 = at least one warning
#   2 = at least one broken model
#   3 = usage error
# ============================================================================

set -u

# ---- args & globals --------------------------------------------------------

ALL_MODE=false
JSON_OUTPUT=false
FORCE_MODE=false
SINGLE_MODEL=""
CTX_REQUESTED=4096
MAX_FILE_MB=8192              # skip models larger than this (8 GB default)
LOG=/tmp/diag_model.log
RESULTS_CSV=/tmp/diag_results.csv

# summary accumulators for --all mode
declare -a SUMMARY_MODELS=()
declare -a SUMMARY_VERDICTS=()
declare -a SUMMARY_RATIOS=()
declare -a SUMMARY_CTXS=()
declare -a SUMMARY_RSS=()
declare -a SUMMARY_GEN_TPS=()
declare -a SUMMARY_FILE_MB=()
declare -i WORST_EXIT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --all)
      ALL_MODE=true
      shift
      ;;
    --json)
      JSON_OUTPUT=true
      shift
      ;;
    --force)
      FORCE_MODE=true
      shift
      ;;
    --max-size)
      MAX_FILE_MB="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '3,30p' "$0"
      exit 0
      ;;
    *)
      if [ -z "$SINGLE_MODEL" ]; then
        SINGLE_MODEL="$1"
      else
        echo "Unknown argument: $1" >&2
        exit 3
      fi
      shift
      ;;
  esac
done

if [ "$ALL_MODE" = "false" ] && [ -z "$SINGLE_MODEL" ]; then
  echo "Usage: $0 <model_name> | --all [--json]"
  exit 3
fi

# ---- helpers ----------------------------------------------------------------

free_mem_mib() {
  awk '/MemAvailable/ { printf "%.0f", $2 / 1024 }' /proc/meminfo
}

peak_rss_mib() {
  ps -eo rss,comm | awk '
    /ollama|llama-server/ { sum += $1 }
    END { if (sum > 0) printf "%.0f", sum / 1024; else print 0 }
  '
}

# file_type u32 → quant name (from llama.cpp llama-hparams.h)
file_type_to_name() {
  case "$1" in
    0)  echo "F32" ;;
    1)  echo "F16" ;;
    2)  echo "Q4_0" ;;
    3)  echo "Q4_1" ;;
    7)  echo "Q8_0" ;;
    8)  echo "Q5_0" ;;
    9)  echo "Q5_1" ;;
    10) echo "Q2_K" ;;
    11) echo "Q3_K_S" ;;
    12) echo "Q3_K_M" ;;
    13) echo "Q3_K_L" ;;
    14) echo "Q4_K_S" ;;
    15) echo "Q4_K_M" ;;
    16) echo "Q5_K_S" ;;
    17) echo "Q5_K_M" ;;
    18) echo "Q6_K" ;;
    19) echo "IQ2_XXS" ;;
    20) echo "IQ2_XS" ;;
    21) echo "Q2_K_S" ;;
    22) echo "IQ3_XS" ;;
    23) echo "IQ3_XXS" ;;
    24) echo "IQ1_S" ;;
    25) echo "IQ4_NL" ;;
    26) echo "IQ3_S" ;;
    27) echo "IQ3_M" ;;
    28) echo "IQ2_S" ;;
    29) echo "IQ2_M" ;;
    30) echo "IQ4_XS" ;;
    31) echo "IQ1_M" ;;
    32) echo "BF16" ;;
    *)  echo "UNKNOWN($1)" ;;
  esac
}

# Guess requested quant from model tag (the part after the colon)
guess_requested_quant() {
  local tag="${1##*:}"
  echo "${tag^^}"
}

# ---- start server once ------------------------------------------------------

# Wait for a port to become free (port 11434)
wait_for_port_free() {
  local port="$1" max_wait="${2:-15}"
  local i=0
  while [ $i -lt $max_wait ]; do
    if ! ss -ltn 2>/dev/null | grep -q ":$port "; then
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

# Wait for ollama process to actually exit (or become a defunct zombie)
# A zombie/defunct process is already dead — it's just waiting for its parent
# to reap it. That's "dead enough" for our purposes (port is free, memory reclaimed).
wait_for_ollama_dead() {
  local max_wait="${1:-15}"
  local i=0
  while [ $i -lt $max_wait ]; do
    # Use -x for exact match — avoids killing this script itself!
    # (script name contains "ollama" as substring)
    # Then filter out zombies (state Z / <defunct>) since those are harmless.
    local live_ollama live_llama
    live_ollama=$(pgrep -x ollama 2>/dev/null | xargs -I{} ps -o pid,stat,comm -p {} 2>/dev/null | grep -v '<defunct>' | grep -v '^PID' | wc -l)
    live_llama=$(pgrep -x llama-server 2>/dev/null | xargs -I{} ps -o pid,stat,comm -p {} 2>/dev/null | grep -v '<defunct>' | grep -v '^PID' | wc -l)
    if [ "$live_ollama" -eq 0 ] && [ "$live_llama" -eq 0 ]; then
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

# Wait for memory to be reclaimed after a kill
wait_for_memory_reclaim() {
  local target_free="${1:-8000}"   # MiB we want available
  local max_wait="${2:-20}"
  local i=0 free_mb
  while [ $i -lt $max_wait ]; do
    free_mb=$(free_mem_mib)
    if [ "$free_mb" -ge "$target_free" ] 2>/dev/null; then
      echo "    Memory reclaimed: ${free_mb} MiB free"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  echo "    ! Memory not fully reclaimed after ${max_wait}s (free: ${free_mb} MiB)"
  return 1
}

# Wait for ollama to be reachable on its API
wait_for_ollama_ready() {
  local max_wait="${1:-20}"
  local i=0
  while [ $i -lt $max_wait ]; do
    if curl -s -o /dev/null -m 2 http://127.0.0.1:11434/api/tags 2>/dev/null; then
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

start_server() {
  echo ">>> Starting ollama with controlled env (ctx=$CTX_REQUESTED, kv=q8_0)..."

  # Try graceful shutdown first, then force kill
  # CRITICAL: use -x for exact match — without it, `pkill ollama` would
  # match this script itself (whose name contains "ollama" as substring)!
  echo "    Stopping any existing ollama..."
  pkill -TERM -x ollama 2>/dev/null
  pkill -TERM -x llama-server 2>/dev/null
  sleep 2
  if pgrep -x ollama >/dev/null 2>&1 || pgrep -x llama-server >/dev/null 2>&1; then
    echo "    Forcing kill..."
    pkill -9 -x ollama 2>/dev/null
    pkill -9 -x llama-server 2>/dev/null
  fi

  # Wait for processes to actually exit
  if ! wait_for_ollama_dead 15; then
    echo "    ✗ Existing ollama/llama-server won't die after 15s"
    ps -eo pid,rss,comm | grep -E 'ollama|llama-server' || true
    exit 3
  fi

  # Wait for port 11434 to be free
  if ! wait_for_port_free 11434 10; then
    echo "    ✗ Port 11434 still in use after 10s"
    ss -ltnp 2>/dev/null | grep 11434 || true
    exit 3
  fi

  # Wait for memory to actually be reclaimed (target: at least 8GB free)
  wait_for_memory_reclaim 8000 20 || true

  rm -f "$LOG"

  OLLAMA_DEBUG=1 \
  OLLAMA_NUM_PARALLEL=1 \
  OLLAMA_MAX_LOADED_MODELS=1 \
  OLLAMA_KEEP_ALIVE=2m \
  OLLAMA_FLASH_ATTENTION=true \
  OLLAMA_CONTEXT_LENGTH=$CTX_REQUESTED \
  OLLAMA_KV_CACHE_TYPE=q8_0 \
  OLLAMA_VULKAN=false \
  ollama serve > "$LOG" 2>&1 < /dev/null &
  SERVER_PID=$!

  # Wait for server to be reachable (replaces fixed sleep 4)
  if ! wait_for_ollama_ready 30; then
    echo "    ✗ Server didn't become reachable in 30s"
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      echo "    Server process died. Last log lines:"
      tail -30 "$LOG"
    else
      echo "    Server process is alive but not responding. Last log lines:"
      tail -30 "$LOG"
    fi
    exit 3
  fi

  echo "    ✓ Server PID=$SERVER_PID up. Free mem: $(free_mem_mib) MiB"
}

# ---- the core check function (returns via global vars) ---------------------
# Sets: DIAG_VERDICT, DIAG_EXIT_CODE, DIAG_RATIO, DIAG_CTX, DIAG_RSS,
#       DIAG_FILE_MB, DIAG_GEN_TPS, DIAG_FILE_TYPE, DIAG_MODEL_PARAMS,
#       DIAG_FALLBACK_MSG, DIAG_CTX_MSG

diagnose_one() {
  local model="$1"
  local verbose="${2:-true}"   # set false in --all mode to reduce output

  DIAG_VERDICT="HEALTHY"
  DIAG_EXIT_CODE=0

  if [ "$verbose" = "true" ]; then
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║  Ollama Model Health Check                                       ║"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo "Model: $model"
    echo "Requested ctx: $CTX_REQUESTED"
    echo ""
    echo ">>> [1/5] Checking model availability..."
  fi

  if ! ollama list | awk '{print $1}' | grep -qx "$model"; then
    if [ "$verbose" = "true" ]; then
      echo "    Model not installed locally. Pulling..."
    fi
    ollama pull "$model" || {
      if [ "$verbose" = "true" ]; then
        echo "    ✗ Failed to pull model"
      fi
      DIAG_VERDICT="BROKEN"
      DIAG_EXIT_CODE=2
      DIAG_RATIO="?"
      DIAG_CTX="?"
      DIAG_RSS="?"
      DIAG_FILE_MB="?"
      DIAG_GEN_TPS="?"
      DIAG_FILE_TYPE="?"
      DIAG_MODEL_PARAMS="?"
      DIAG_FALLBACK_MSG="Failed to pull model"
      DIAG_CTX_MSG=""
      return
    }
  fi

  # File size from ollama list (handle "1.9 GB" or "701 MB" formats)
  local size_value size_unit
  size_value=$(ollama list | awk -v m="$model" '$1==m { print $3 }')
  size_unit=$(ollama list | awk -v m="$model" '$1==m { print $4 }')
  local file_size_mb
  case "$size_unit" in
    GB|GiB) file_size_mb=$(echo "$size_value" | awk '{print $1 * 1024}') ;;
    MB|MiB) file_size_mb="$size_value" ;;
    KB|KiB) file_size_mb=$(echo "$size_value" | awk '{print $1 / 1024}') ;;
    *)      file_size_mb="$size_value" ;;
  esac

  # ---- MEMORY SAFETY CHECKS (skip models that would OOM) ----
  if [ "$FORCE_MODE" = "false" ]; then
    # Check 1: file size > MAX_FILE_MB → skip
    if [ -n "$file_size_mb" ] && [ "$file_size_mb" != "?" ]; then
      if (( $(echo "$file_size_mb > $MAX_FILE_MB" | bc -l 2>/dev/null || echo 0) )); then
        if [ "$verbose" = "true" ]; then
          local file_gb
          file_gb=$(echo "scale=2; $file_size_mb / 1024" | bc)
          echo "    ⏭  SKIPPED: file size ${file_gb} GB exceeds --max-size ${MAX_FILE_MB} MB"
          echo "       (use --force or --max-size $((file_size_mb + 1024)) to test this model)"
        fi
        DIAG_VERDICT="SKIPPED"
        DIAG_EXIT_CODE=0
        DIAG_RATIO="?"
        DIAG_CTX="?"
        DIAG_RSS="?"
        DIAG_FILE_MB="$file_size_mb"
        DIAG_GEN_TPS="?"
        DIAG_FILE_TYPE="?"
        DIAG_MODEL_PARAMS="?"
        DIAG_FALLBACK_MSG="Skipped: file size ${file_size_mb} MB > max ${MAX_FILE_MB} MB"
        DIAG_CTX_MSG=""
        return
      fi
    fi

    # Check 2: free memory < file_size * 1.5 → skip (would OOM)
    local free_mb required_mb
    free_mb=$(free_mem_mib)
    required_mb=$(echo "scale=0; $file_size_mb * 1.5 / 1" | bc 2>/dev/null)
    if [ -n "$free_mb" ] && [ -n "$required_mb" ] && [ "$free_mb" != "0" ]; then
      if (( $(echo "$free_mb < $required_mb" | bc -l 2>/dev/null || echo 0) )); then
        if [ "$verbose" = "true" ]; then
          echo "    ⏭  SKIPPED: would OOM (free: ${free_mb} MiB, need: ${required_mb} MiB for file+KV)"
          echo "       (use --force to attempt anyway)"
        fi
        DIAG_VERDICT="SKIPPED"
        DIAG_EXIT_CODE=0
        DIAG_RATIO="?"
        DIAG_CTX="?"
        DIAG_RSS="?"
        DIAG_FILE_MB="$file_size_mb"
        DIAG_GEN_TPS="?"
        DIAG_FILE_TYPE="?"
        DIAG_MODEL_PARAMS="?"
        DIAG_FALLBACK_MSG="Skipped: free mem ${free_mb} MiB < required ${required_mb} MiB"
        DIAG_CTX_MSG=""
        return
      fi
    fi
  fi

  if [ "$verbose" = "true" ]; then
    local file_size_display
    if (( $(echo "$file_size_mb >= 1024" | bc -l) )); then
      file_size_display=$(echo "scale=2; $file_size_mb / 1024" | bc)" GB"
    else
      file_size_display="$file_size_mb MB"
    fi
    echo "    ✓ Installed"
    echo "    File size: $file_size_display ($file_size_mb MB)"
    echo ""
    echo ">>> [2/5] Server already running with controlled env (ctx=$CTX_REQUESTED, kv=q8_0)"
    echo ""
    echo ">>> [3/5] Running inference test..."
    echo "    Prompt: \"Say hello in one short sentence.\""
    echo ""
  fi

  # Capture log line count BEFORE this model loads
  local log_start log_end
  log_start=$(wc -l < "$LOG" 2>/dev/null || echo 1)
  log_start=$((log_start + 1))

  # Unload any previous model
  pkill -9 -x llama-server 2>/dev/null
  sleep 2

  local start_time end_time exit_code wall_time output
  start_time=$(date +%s.%N)
  output=$(timeout 120 ollama run "$model" "Say hello in one short sentence." 2>&1 < /dev/null)
  exit_code=$?
  end_time=$(date +%s.%N)
  wall_time=$(echo "$end_time - $start_time" | bc)

  log_end=$(wc -l < "$LOG" 2>/dev/null || echo 1)

  local peak_rss
  peak_rss=$(peak_rss_mib)

  if [ "$exit_code" -ne 0 ]; then
    if [ "$verbose" = "true" ]; then
      echo "    ✗ Inference failed (exit $exit_code)"
      echo "    Last 20 log lines:"
      sed -n "${log_start},${log_end}p" "$LOG" | tail -20
    fi
    DIAG_VERDICT="BROKEN"
    DIAG_EXIT_CODE=2
    DIAG_RATIO="?"
    DIAG_CTX="?"
    DIAG_RSS="$peak_rss"
    DIAG_FILE_MB="$file_size_mb"
    DIAG_GEN_TPS="?"
    DIAG_FILE_TYPE="?"
    DIAG_MODEL_PARAMS="?"
    DIAG_FALLBACK_MSG="Inference failed (exit $exit_code) — likely OOM kill"
    DIAG_CTX_MSG=""
    return
  fi

  if [ "$verbose" = "true" ]; then
    echo "    Output: $(echo "$output" | head -1)..."
    echo "    Wall time: ${wall_time}s"
    echo "    Peak RSS: ${peak_rss} MiB"
    echo ""
  fi

  # ---- extract metadata from log slice -------------------------------------

  local log_slice_text
  log_slice_text=$(sed -n "${log_start},${log_end}p" "$LOG" 2>/dev/null)

  # file_type from GGUF metadata
  local file_type_num metadata_quant
  file_type_num=$(echo "$log_slice_text" | grep -m1 -oE 'general\.file_type u32 *= *[0-9]+' | grep -oE '[0-9]+' | head -1)
  if [ -n "$file_type_num" ]; then
    metadata_quant=$(file_type_to_name "$file_type_num")" (ftype=$file_type_num)"
  else
    metadata_quant="?"
  fi

  # actual context used
  local actual_ctx
  actual_ctx=$(echo "$log_slice_text" | grep -m1 -oE 'n_ctx_slot *= *[0-9]+' | grep -oE '[0-9]+' | head -1)
  [ -z "$actual_ctx" ] && actual_ctx="?"

  # model params
  local model_params
  model_params=$(echo "$log_slice_text" | grep -m1 -oE 'model params *= *[0-9.]+ *[BM]' | sed -E 's/.* = *//')
  [ -z "$model_params" ] && model_params="?"

  # tok/s
  local prompt_tps gen_tps
  prompt_tps=$(echo "$log_slice_text" | grep -m1 'prompt eval time' | grep -oE '[0-9]+\.[0-9]+ tokens per second' | head -1)
  [ -z "$prompt_tps" ] && prompt_tps="?"
  gen_tps=$(echo "$log_slice_text" | grep 'eval time' | grep -v 'prompt eval' | head -1 \
              | grep -oE '[0-9]+\.[0-9]+ tokens per second' | head -1)
  [ -z "$gen_tps" ] && gen_tps="?"

  # expansion ratio = peak_rss / file_size_mb
  local ratio ratio_num
  if [ -n "$file_size_mb" ] && [ "$file_size_mb" != "0" ] && [ -n "$peak_rss" ] && [ "$peak_rss" != "0" ]; then
    ratio=$(echo "scale=2; $peak_rss / $file_size_mb" | bc 2>/dev/null)
  else
    ratio="?"
  fi

  # ---- health analysis -----------------------------------------------------

  local requested_quant fallback_status fallback_msg ctx_status ctx_msg meta_status meta_msg perf_status perf_msg
  requested_quant=$(guess_requested_quant "$model")

  if [ "$verbose" = "true" ]; then
    echo ">>> [4/5] Health analysis"
    echo ""
    echo "    [Check 1] fp16 fallback detection"
    echo "      Requested quant (from tag): $requested_quant"
    echo "      GGUF metadata quant: $metadata_quant"
    echo "      File size: $file_size_mb MB"
    echo "      Peak RSS:  $peak_rss MiB"
    echo "      Expansion ratio (RSS/file): $ratio"
  fi

  ratio_num=$(echo "$ratio" | grep -oE '[0-9.]+' | head -1)
  if [ -n "$ratio_num" ]; then
    if (( $(echo "$ratio_num >= 2.0" | bc -l) )); then
      fallback_status="FAIL"
      fallback_msg="Likely fp16 fallback (2x+ expansion). Model is loading as fp16 internally."
    elif (( $(echo "$ratio_num >= 1.5" | bc -l) )); then
      fallback_status="WARN"
      fallback_msg="Suspicious expansion (1.5-2x). May be KV cache overhead or partial fallback."
    else
      fallback_status="PASS"
      fallback_msg="Clean load (file size ≈ runtime memory)."
    fi
  else
    fallback_status="UNKNOWN"
    fallback_msg="Could not compute ratio."
  fi

  if [ "$verbose" = "true" ]; then
    echo "      Result: $fallback_status — $fallback_msg"
    echo ""
    echo "    [Check 2] Context override detection"
    echo "      Requested ctx: $CTX_REQUESTED"
    echo "      Actual ctx:   $actual_ctx"
  fi

  if [ "$actual_ctx" != "?" ] && [ "$actual_ctx" != "$CTX_REQUESTED" ]; then
    ctx_status="FAIL"
    ctx_msg="Model metadata is forcing ctx=$actual_ctx, overriding your env var!"
  else
    ctx_status="PASS"
    ctx_msg="Context matches requested value."
  fi

  if [ "$verbose" = "true" ]; then
    echo "      Result: $ctx_status — $ctx_msg"
    echo ""
    echo "    [Check 3] GGUF metadata honesty"
  fi

  if [ "$metadata_quant" != "?" ] && echo "$metadata_quant" | grep -q "BF16\|F16\|F32"; then
    if echo "$requested_quant" | grep -qE 'IQ|Q[0-9]'; then
      meta_status="WARN"
      meta_msg="Metadata claims $metadata_quant but tag requests $requested_quant (common for mixed-quant GGUFs — not necessarily broken)."
    else
      meta_status="PASS"
      meta_msg="Metadata matches tag."
    fi
  else
    meta_status="PASS"
    meta_msg="Metadata reports $metadata_quant."
  fi

  if [ "$verbose" = "true" ]; then
    echo "      Result: $meta_status — $meta_msg"
    echo ""
    echo "    [Check 4] Performance sanity"
  fi

  if [ "$gen_tps" != "?" ]; then
    local gen_tps_num
    gen_tps_num=$(echo "$gen_tps" | grep -oE '[0-9.]+' | head -1)
    if (( $(echo "$gen_tps_num < 1.0" | bc -l) )); then
      perf_status="WARN"
      perf_msg="Slow generation (${gen_tps}). Usable but painful for interactive use."
    elif (( $(echo "$gen_tps_num < 3.0" | bc -l) )); then
      perf_status="PASS"
      perf_msg="Acceptable for CPU (${gen_tps})."
    else
      perf_status="PASS"
      perf_msg="Good CPU performance (${gen_tps})."
    fi
  else
    perf_status="UNKNOWN"
    perf_msg="Could not extract tok/s."
  fi

  if [ "$verbose" = "true" ]; then
    echo "      Generation tok/s: $gen_tps"
    echo "      Result: $perf_status — $perf_msg"
    echo ""
  fi

  # ---- final verdict -------------------------------------------------------

  local overall overall_code
  overall="HEALTHY"
  overall_code=0
  [ "$fallback_status" = "WARN" ] && [ "$overall" = "HEALTHY" ] && overall="WARNING"; overall_code=1
  [ "$fallback_status" = "FAIL" ] && overall="BROKEN"; overall_code=2
  [ "$ctx_status" = "FAIL" ] && [ "$overall" != "BROKEN" ] && overall="WARNING"; overall_code=1
  [ "$perf_status" = "WARN" ] && [ "$overall" = "HEALTHY" ] && overall="WARNING"; overall_code=1

  DIAG_VERDICT="$overall"
  DIAG_EXIT_CODE="$overall_code"
  DIAG_RATIO="$ratio"
  DIAG_CTX="$actual_ctx"
  DIAG_RSS="$peak_rss"
  DIAG_FILE_MB="$file_size_mb"
  DIAG_GEN_TPS="$gen_tps"
  DIAG_FILE_TYPE="$metadata_quant"
  DIAG_MODEL_PARAMS="$model_params"
  DIAG_FALLBACK_MSG="$fallback_msg"
  DIAG_CTX_MSG="$ctx_msg"

  if [ "$verbose" = "true" ]; then
    local emoji summary
    case "$overall" in
      HEALTHY)  emoji="✅"; summary="Model loads cleanly. Safe to use in AgentKthx." ;;
      WARNING)  emoji="⚠️";  summary="Model works but has issues. See advice below." ;;
      BROKEN)   emoji="❌"; summary="Model is broken. Avoid using it." ;;
    esac

    echo ">>> [5/5] Final verdict"
    echo ""
    echo "╔══════════════════════════════════════════════════════════════════╗"
    echo "║  $emoji VERDICT: $overall"
    echo "║  $summary"
    echo "╚══════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Metrics summary:"
    echo "  Model:           $model"
    echo "  Model params:    $model_params"
    echo "  File size:       $file_size_mb MB"
    echo "  Peak RSS:        $peak_rss MiB"
    echo "  Expansion ratio:  $ratio"
    echo "  Metadata quant:  $metadata_quant"
    echo "  Requested ctx:   $CTX_REQUESTED"
    echo "  Actual ctx:      $actual_ctx"
    echo "  Prompt tok/s:    $prompt_tps"
    echo "  Gen tok/s:       $gen_tps"
    echo "  Wall time:       ${wall_time}s"
    echo ""

    if [ "$ctx_status" = "FAIL" ]; then
      echo "🔧 Recommended fix:"
      echo "  Add 'PARAMETER num_ctx $CTX_REQUESTED' to a Modelfile and use 'ollama create' to derive a fixed model."
      echo ""
    fi
  fi
}

# ---- single model mode ------------------------------------------------------

if [ "$ALL_MODE" = "false" ]; then
  start_server
  diagnose_one "$SINGLE_MODEL" true
  pkill -9 -x llama-server 2>/dev/null
  exit $DIAG_EXIT_CODE
fi

# ---- all models mode --------------------------------------------------------

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  Ollama Model Health Check — ALL MODE                            ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo "Requested ctx: $CTX_REQUESTED"
echo "Max file size: $MAX_FILE_MB MB (use --max-size to change, --force to disable)"
echo ""

# Start server FIRST (before listing models — ollama list requires a server)
start_server
echo ""

# Now discover installed models
echo ">>> Discovering installed models..."
mapfile -t MODELS < <(ollama list | tail -n +2 | awk '{print $1}')
echo ">>> Found ${#MODELS[@]} models to test:"
for m in "${MODELS[@]}"; do echo "  - $m"; done
echo ""

# Init CSV
echo "model,verdict,exit_code,file_mb,peak_rss_mib,expansion_ratio,actual_ctx,gen_tps,metadata_quant,model_params" \
  > "$RESULTS_CSV"

# Iterate
i=0
for model in "${MODELS[@]}"; do
  [ -z "$model" ] && continue
  i=$((i + 1))
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "[$i/${#MODELS[@]}] $model"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Verbose=false to keep output compact per model
  diagnose_one "$model" false

  # Capture into summary arrays
  SUMMARY_MODELS+=("$model")
  SUMMARY_VERDICTS+=("$DIAG_VERDICT")
  SUMMARY_RATIOS+=("$DIAG_RATIO")
  SUMMARY_CTXS+=("$DIAG_CTX")
  SUMMARY_RSS+=("$DIAG_RSS")
  SUMMARY_GEN_TPS+=("$DIAG_GEN_TPS")
  SUMMARY_FILE_MB+=("$DIAG_FILE_MB")

  # Update worst exit code
  if [ "$DIAG_EXIT_CODE" -gt "$WORST_EXIT" ]; then
    WORST_EXIT=$DIAG_EXIT_CODE
  fi

  # Print compact result
  emoji=""
  case "$DIAG_VERDICT" in
    HEALTHY)  emoji="✅" ;;
    WARNING)  emoji="⚠️"  ;;
    BROKEN)   emoji="❌" ;;
    SKIPPED)  emoji="⏭️" ;;
    *)        emoji="?" ;;
  esac
  echo "  $emoji $DIAG_VERDICT — ratio: $DIAG_RATIO | ctx: $DIAG_CTX | RSS: ${DIAG_RSS} MiB | gen_tps: $DIAG_GEN_TPS"
  if [ -n "$DIAG_FALLBACK_MSG" ] && [ "$DIAG_VERDICT" != "HEALTHY" ]; then
    echo "     ↳ $DIAG_FALLBACK_MSG"
  fi
  if [ -n "$DIAG_CTX_MSG" ] && [ "$DIAG_CTX_MSG" != "Context matches requested value." ]; then
    echo "     ↳ $DIAG_CTX_MSG"
  fi
  echo ""

  # append to CSV
  echo "$model,$DIAG_VERDICT,$DIAG_EXIT_CODE,$DIAG_FILE_MB,$DIAG_RSS,$DIAG_RATIO,$DIAG_CTX,$DIAG_GEN_TPS,$DIAG_FILE_TYPE,$DIAG_MODEL_PARAMS" \
    >> "$RESULTS_CSV"

  sleep 2
done

pkill -9 llama-server 2>/dev/null

# ---- final summary table ----------------------------------------------------

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  FINAL SUMMARY — All Models                                     ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# count verdicts
healthy=0; warning=0; broken=0; skipped=0
for v in "${SUMMARY_VERDICTS[@]}"; do
  case "$v" in
    HEALTHY) healthy=$((healthy + 1)) ;;
    WARNING) warning=$((warning + 1)) ;;
    BROKEN)  broken=$((broken + 1)) ;;
    SKIPPED) skipped=$((skipped + 1)) ;;
  esac
done

echo "Total: ${#SUMMARY_MODELS[@]}  |  ✅ Healthy: $healthy  |  ⚠️ Warning: $warning  |  ❌ Broken: $broken  |  ⏭️ Skipped: $skipped"
echo ""

# Print table
printf "%-55s %-10s %-8s %-10s %-10s %-10s\n" "MODEL" "VERDICT" "RATIO" "CTX" "RSS_MiB" "GEN_TPS"
printf "%-55s %-10s %-8s %-10s %-10s %-10s\n" "$(printf '%0.s-' {1..55})" "----------" "--------" "----------" "----------" "----------"

for idx in "${!SUMMARY_MODELS[@]}"; do
  m="${SUMMARY_MODELS[$idx]}"
  v="${SUMMARY_VERDICTS[$idx]}"
  r="${SUMMARY_RATIOS[$idx]}"
  c="${SUMMARY_CTXS[$idx]}"
  rss="${SUMMARY_RSS[$idx]}"
  tps="${SUMMARY_GEN_TPS[$idx]}"

  # truncate model name if too long
  if [ ${#m} -gt 53 ]; then
    m_disp="...${m: -50}"
  else
    m_disp="$m"
  fi

  # mark broken with X
  case "$v" in
    HEALTHY)  marker=" " ;;
    WARNING)  marker="!" ;;
    BROKEN)   marker="X" ;;
    SKIPPED)  marker="~" ;;
    *)        marker="?" ;;
  esac

  printf "%-55s %s%-9s %-8s %-10s %-10s %-10s\n" "$m_disp" "$marker" "$v" "$r" "$c" "$rss" "$tps"
done

echo ""
echo "Legend: X = BROKEN, ! = WARNING, ~ = SKIPPED, (space) = HEALTHY"
echo ""
echo "Detailed CSV: $RESULTS_CSV"
echo ""

# JSON output if requested
if [ "$JSON_OUTPUT" = "true" ]; then
  echo "{"
  echo "  \"total\": ${#SUMMARY_MODELS[@]},"
  echo "  \"healthy\": $healthy,"
  echo "  \"warning\": $warning,"
  echo "  \"broken\": $broken,"
  echo "  \"skipped\": $skipped,"
  echo "  \"models\": ["
  for idx in "${!SUMMARY_MODELS[@]}"; do
    m="${SUMMARY_MODELS[$idx]}"
    v="${SUMMARY_VERDICTS[$idx]}"
    r="${SUMMARY_RATIOS[$idx]}"
    c="${SUMMARY_CTXS[$idx]}"
    rss="${SUMMARY_RSS[$idx]}"
    tps="${SUMMARY_GEN_TPS[$idx]}"
    fmb="${SUMMARY_FILE_MB[$idx]}"
    comma=","
    if [ $((idx + 1)) -eq ${#SUMMARY_MODELS[@]} ]; then comma=""; fi
    echo "    {\"model\": \"$m\", \"verdict\": \"$v\", \"ratio\": \"$r\", \"ctx\": \"$c\", \"rss_mib\": \"$rss\", \"gen_tps\": \"$tps\", \"file_mb\": \"$fmb\"}$comma"
  done
  echo "  ]"
  echo "}"
fi

exit $WORST_EXIT
