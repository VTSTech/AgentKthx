#!/usr/bin/env python3
"""probe_duckduckgo.py — DuckDuckGo AI Chat backend live probe (2026-10 protocol).

Drives the duckduckgo PLUGIN BACKEND itself from an unrestricted
network — TEST-13's automation. The old x-vqd-4 probe is retired with
the protocol; this one exercises the real code path end-to-end:

  1. Proof check   — /status challenge fetch → Node solve → proof build
  2. Capabilities  — GET /duckchat/v1/capabilities (live model catalog)
  3. Model sweep   — one minimal generate() per seed model
  4. Seed emit     — a model_seed.json-compatible JSON block (--json)

Requires: node on PATH (challenge solver + durableStream keygen —
same runtime dependency as the backend). Stdlib otherwise.

Usage:
    python3 scripts/probe_duckduckgo.py            # human-readable
    python3 scripts/probe_duckduckgo.py --json     # seed-catalog block
    python3 scripts/probe_duckduckgo.py --model gpt-6-luna   # one model
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.plugins.duckduckgo.duckduckgo import (  # noqa: E402
    _WIRE_MODELS,
    DuckDuckGoBackend,
)

#: Seed metadata merged into the --json emit (mirrors update_ddg_seed.py).
_SEED_DEFAULTS = {
    "context_length": 128000,
    "default_temperature": 0.7,
    "default_max_tokens": 16384,
    "free_tier": True,
}


def probe_proof(backend: DuckDuckGoBackend) -> bool:
    """Challenge fetch → solve → build (the x-vqd-hash-1 handshake)."""
    print("== proof check: /status challenge -> node solve -> header build ==")
    t0 = time.monotonic()
    try:
        challenge = backend._fetch_challenge()
        raw = backend._solve_challenge(challenge)
        header = backend._build_proof_header(raw, (time.monotonic() - t0) * 1000)
    except Exception as e:  # noqa: BLE001 — probe tool reports everything
        print(f"  FAIL: {e}")
        return False
    cid = raw.get("meta", {}).get("challenge_id", "")[:16]
    probes = raw.get("client_hashes", [None, None, None])[1:]
    print(f"  ok: challenge {cid}… probes={probes} -> {len(header)}-char X-Vqd-Hash-1")
    return True


def probe_capabilities(backend: DuckDuckGoBackend) -> dict | None:
    """Live model catalog via GET /duckchat/v1/capabilities."""
    print("== capabilities: live catalog ==")
    caps = backend.fetch_capabilities()
    if caps is None:
        print("  unavailable (soft-failed) — seed catalog remains authoritative")
        return None
    print(f"  {json.dumps(caps, indent=2)[:1200]}")
    return caps


def probe_models(backend: DuckDuckGoBackend, only: str | None) -> dict[str, str]:
    """One minimal generate() per seed model → {model: reply-or-error}."""
    targets = [m["id"] for m in _WIRE_MODELS if not only or m["id"] == only]
    print(f"== model sweep: {len(targets)} model(s) ==")
    results: dict[str, str] = {}
    for wire_id in targets:
        try:
            t0 = time.monotonic()
            out = backend.generate(wire_id, [{"role": "user", "content": "Say exactly: pong"}])
            ms = (time.monotonic() - t0) * 1000
            ok = "pong" in (out.get("content") or "").lower()
            results[wire_id] = "ok" if ok else "reply"
            print(
                f"  [{'OK' if ok else 'REPLY'}] {wire_id} ({ms:.0f}ms): "
                f"{(out.get('content') or '')[:60]!r}"
            )
        except Exception as e:  # noqa: BLE001 — every failure is data
            results[wire_id] = f"error: {e}"
            print(f"  [FAIL] {wire_id}: {e}")
        time.sleep(2)  # per-IP throttle courtesy
    return results


def emit_seed(results: dict[str, str]) -> None:
    """Print a model_seed.json-compatible `duckduckgo` block."""
    seed = {}
    for wire_id, verdict in results.items():
        if verdict not in ("ok", "reply"):
            continue  # seed only what answers — the house directive
        entry = dict(_SEED_DEFAULTS)
        entry["family"] = wire_id.split("/")[0].split("-")[0]
        entry["note"] = f"live-probed {time.strftime('%Y-%m-%d')} ({verdict})"
        seed[wire_id] = entry
    print("\n== model_seed.json `duckduckgo` block ==")
    print(json.dumps({"duckduckgo": seed}, indent=1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit a seed-catalog block")
    parser.add_argument("--model", help="probe a single wire ID instead of the sweep")
    args = parser.parse_args()

    backend = DuckDuckGoBackend()
    if not probe_proof(backend):
        return 1
    probe_capabilities(backend)
    results = probe_models(backend, args.model)
    if args.json:
        emit_seed(results)

    good = sum(1 for v in results.values() if v in ("ok", "reply"))
    print(f"\nsummary: {good}/{len(results)} models answered")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
