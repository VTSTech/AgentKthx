#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentKthx — API Technical Reference Validation Probe Scripts
# ═══════════════════════════════════════════════════════════════════════════════
# One script per backend. All scripts use GET-only endpoints (no inference
# calls, no tokens burned, no paid API requests). Each script:
#
#   1. Probes the backend's model-discovery endpoint (/models, /v1/models, etc.)
#   2. Extracts pricing / context_length / max_output_tokens fields from the
#      response (if exposed by the API)
#   3. Counts chat-capable vs non-chat models (using the backend's own filter
#      or a simple heuristic)
#   4. Compares against the static catalog in the AgentKthx plugin source
#   5. Reports discrepancies (models in the live API but not in the static
#      catalog, and vice versa)
#
# Usage:
#   bash probe_openai.sh       # requires OPENAI_API_KEY in env
#   bash probe_huggingface.sh  # requires HF_TOKEN in env (optional — /v1/models is anonymous)
#   bash probe_openrouter.sh   # requires OPENROUTER_API_KEY in env (optional — /models is anonymous)
#   bash probe_gemini.sh       # requires GEMINI_API_KEY in env
#   bash probe_zai.sh          # requires ZAI_API_KEY in env
#   bash probe_ollama.sh       # requires Ollama running on localhost:11434
#   bash probe_llama_server.sh # requires llama-server running on localhost:8764
#   bash probe_bitnet.sh       # requires BitNet server running (same as llama-server)
#
# All scripts save their JSON output to /tmp/agentkthx_probe_<backend>.json
# for later analysis. The scripts are safe to re-run — they're read-only GET
# probes that don't modify any state.
#
# Written by VTSTech — https://www.vts-tech.org
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Helper: print a section header
header() {
    echo ""
    echo -e "${CYAN}${BOLD}═══ $1 ═══${NC}"
}

# Helper: print a key-value pair
kv() {
    printf "  %-40s %s\n" "$1:" "$2"
}

# Helper: check if a command exists
require() {
    if ! command -v "$1" &>/dev/null; then
        echo -e "${RED}ERROR: '$1' not found. Install it or add to PATH.${NC}" >&2
        exit 1
    fi
}

require curl
require python3
