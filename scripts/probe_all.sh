#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# AgentKthx — API Technical Reference Validation Probe Scripts
# ═══════════════════════════════════════════════════════════════════════════════
# One script per backend. All scripts use GET-only endpoints (no inference
# calls, no tokens burned, no paid API requests). Each script produces a
# 6-section report matching the format established by probe_mistral.sh:
#
#   1. Endpoint              — base URL, auth shape, HTTP status, raw JSON path
#   2. Static API surface     — request params table, response shape table,
#                               tool calling format block (hardcoded from
#                               docs/api/*_API_TECHNICAL_REFERENCE.md)
#   3. Live /v1/models        — total count, top-level keys, sample card
#   4. Card field availability — per-field ✓ all / ~ partial / ○ missing matrix
#   5. Per-model detail       — id, family, ctx, max_tok, size, pricing,
#                               is_free, is_paid (uniform table)
#   6. Free vs full catalog    — free/paid split, catalog drift, deprecation
#                               warnings (LOCAL backends: installed vs running)
#
# Cloud backends (7): openai, gemini, mistral, zai, openrouter, huggingface,
#                    pollinations — full 11-field treatment including pricing
#                    and free/paid classification.
# Local backends (3): ollama, llama_server, bitnet — pricing/free/paid are
#                     marked "N/A" (no concept of paid local models). Section 6
#                     shows installed vs running models instead of free/paid.
#
# Usage:
#   bash probe_mistral.sh       # requires MISTRAL_API_KEY in env
#   bash probe_openai.sh       # requires OPENAI_API_KEY in env
#   bash probe_gemini.sh       # requires GEMINI_API_KEY (or GOOGLE_API_KEY)
#   bash probe_zai.sh          # requires ZAI_API_KEY in env
#   bash probe_openrouter.sh   # OPENROUTER_API_KEY optional (/models is anonymous)
#   bash probe_huggingface.sh  # HF_TOKEN optional (/v1/models is anonymous)
#   bash probe_pollinations.sh # POLLINATIONS_API_KEY optional (runs keyless)
#   bash probe_ollama.sh       # requires Ollama running on localhost:11434
#   bash probe_llama_server.sh # requires llama-server running on localhost:8764
#   bash probe_bitnet.sh       # requires BitNet server running on localhost:8765
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
