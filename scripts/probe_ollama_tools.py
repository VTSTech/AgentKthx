#!/usr/bin/env python3
"""
Probe Ollama tool support via the server's own `capabilities` field.

Modern Ollama reports a per-model `capabilities` array in GET /api/tags
(e.g. ["completion", "tools", "insert"]). The server derives it from the
model's chat template / GGUF metadata — the exact same source its runner
uses to decide whether tool calling works. So:

    "tools" in capabilities  ->  native tool calling is supported
    otherwise                ->  model cannot call tools

This is authoritative, O(1), and loads no models — replacing the old
sample-based probe (one "What's the weather in Tokyo?" request per model,
max_tokens=100, no system prompt), which produced false negatives for
capable-but-chatty models.

Optionally cross-references the local AgentKthx tool-support cache
(written by `agentkthx models --tool-support`) to show where the old
sampled verdict disagrees with the declared capability.

Usage:
    python3 probe_ollama_tools.py [base_url] [options]

Environment:
    OLLAMA_BASE_URL   default base URL (default: http://localhost:11434)

Examples:
    python3 probe_ollama_tools.py
    python3 probe_ollama_tools.py http://localhost:11434 --json
    python3 probe_ollama_tools.py --no-cache

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GB = 1024**3


def http_get_json(url: str, timeout: int) -> dict | None:
    """GET a URL and parse the body as JSON. None on any failure."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def fmt_size(size: int) -> str:
    return f"{size / GB:.2f} GB" if size else "—"


def load_cached_verdicts() -> dict[str, str]:
    """Read the AgentKthx tool-support cache (openre keys) if present.

    The cache is written by `agentkthx models --tool-support` and keyed by
    bare model name for the openre (native /api/chat) mode. Returns a map
    of model name -> verdict string ("native" / "react" / "none" / ...).
    """
    base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    cache_file = Path(base) / "agentkthx" / "tool_support.json"
    if not cache_file.exists():
        return {}
    try:
        raw = json.loads(cache_file.read_text())
    except Exception:
        return {}
    verdicts: dict[str, str] = {}
    for key, entry in raw.items():
        if isinstance(entry, dict) and ":openai" not in key:
            verdicts[key] = entry.get("support", "?")
    return verdicts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Determine Ollama tool support from /api/tags capabilities"
    )
    parser.add_argument(
        "base_url",
        nargs="?",
        default=None,
        help="Ollama base URL (default: $OLLAMA_BASE_URL or http://localhost:11434)",
    )
    parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout in seconds")
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Emit JSON lines instead of a table"
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Skip the cross-reference against AgentKthx's cached probe verdicts",
    )
    args = parser.parse_args()

    base_url = (
        args.base_url or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434"
    ).rstrip("/")
    tags_url = f"{base_url}/api/tags"

    data = http_get_json(tags_url, timeout=args.timeout)
    if data is None:
        print(f"❌ Could not reach Ollama at {tags_url}")
        print("   Start with: ollama serve  (or set OLLAMA_BASE_URL)")
        return 1

    models = data.get("models", [])
    if not models:
        print(f"No models found at {tags_url}")
        return 0

    # Old servers omit `capabilities` entirely — detect and warn once.
    caps_available = any("capabilities" in m for m in models)

    cached = {} if args.no_cache else load_cached_verdicts()

    rows = []
    for m in sorted(models, key=lambda m: m["name"]):
        details = m.get("details", {}) or {}
        caps = m.get("capabilities", []) or []
        has_tools = "tools" in caps
        name = m["name"]
        cached_verdict = cached.get(name)
        # Disagreement: the old sampled probe misclassified a capable model.
        delta = bool(cached_verdict and has_tools and cached_verdict != "native")
        rows.append(
            {
                "name": name,
                "family": details.get("family", "unknown"),
                "quant": details.get("quantization_level", "") or "—",
                "size": fmt_size(m.get("size", 0)),
                "parameter_size": details.get("parameter_size", "—"),
                "capabilities": caps,
                "verdict": "native" if has_tools else "none",
                "cached": cached_verdict,
                "delta": delta,
            }
        )

    if args.as_json:
        for row in rows:
            print(json.dumps(row))
        return 0

    name_w = max(len(r["name"]) for r in rows) + 2
    header = (
        f"  {'Model':<{name_w}} {'Fam':<9} {'Quant':<7} {'Size':>9}  "
        f"{'Verdict':<9} {'Cached(bare)':<12} Caps"
    )
    print()
    print(f"⚖ AgentKthx — Ollama Tool Support (from /api/tags capabilities)")
    print(f"  Server: {tags_url}")
    print(
        f"  Cache:  {'(skipped)' if args.no_cache else '(AgentKthx tool_support.json, openre keys)'}"
    )
    print("-" * (len(header) - 2))
    print(header)
    print("-" * (len(header) - 2))
    for r in rows:
        verdict = "✓ native" if r["verdict"] == "native" else "✗ none"
        cached_col = r["cached"] if r["cached"] else "—"
        if r["delta"]:
            cached_col += " !"
        print(
            f"  {r['name']:<{name_w}} {r['family']:<9} {r['quant']:<7} {r['size']:>9}  "
            f"{verdict:<9} {cached_col:<12} {','.join(r['capabilities'])}"
        )
    print("-" * (len(header) - 2))
    native_n = sum(1 for r in rows if r["verdict"] == "native")
    print(f"Total: {len(rows)} models — {native_n} native, {len(rows) - native_n} none")
    if not caps_available:
        print(
            "⚠ Server did not report capabilities for any model — Ollama may be too old. "
            "Upgrade Ollama for this probe to work."
        )
    print()
    print("Legend: ✓ native = 'tools' in /api/tags capabilities | ✗ none = capability absent")
    print("        Cached(bare) = verdict from the old no-prompt sampled probe (local cache)")
    print("        '!' marks models where the cached verdict disagrees with the declared")
    print("        capability — those are the false negatives the sampled probe produced.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
