#!/usr/bin/env python3
"""probe_duckduckgo.py — DuckDuckGo AI Chat backend probe.

Live discovery + smoke probe for the duckduckgo plugin scaffold:

  1. Bootstrap an x-vqd-4 token (GET /duckchat/v1/status)
  2. Discover the live model list (duck.ai JS bundle grep + candidate probing)
  3. Probe each candidate model ID with a minimal /duckchat/v1/chat call
  4. Print a seed-catalog JSON block for agentkthx/data/model_seed.json

Stdlib only (house rule). Usage:
    python3 scripts/probe_duckduckgo.py [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "https://duckduckgo.com"
STATUS_URL = f"{BASE_URL}/duckchat/v1/status"
CHAT_URL = f"{BASE_URL}/duckchat/v1/chat"

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Candidate model IDs to probe — union of:
#   - mrgick/duck_chat (older reverse-engineered set)
#   - @mumulhl/duckduckgo-ai-chat v3.3.0 JSR set
#   - Oct 2026 help-page models (guessed ID shapes, marked *)
CANDIDATES = [
    "gpt-4o-mini",
    "claude-3-haiku-20240307",
    "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
    "mistralai/Mixtral-8x7B-Instruct-v0.1",
    "o3-mini",
    # help-page era (Oct 2026): Claude 4.5 Haiku / Llama 3.3 70B /
    # Mistral Small 3 24B — ID shapes guessed, probe decides
    "claude-4-5-haiku",
    "claude-4-5-haiku-20251001",
    "anthropic/claude-4-5-haiku",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "meta-llama/Meta-Llama-3.3-70B-Instruct-Turbo",
    "mistralai/Mistral-Small-24B-Instruct-2501",
    "mistral-small-3",
]

JS_BUNDLE_CANDIDATES = [
    "https://duckduckgo.com/duckchat/v1/models",  # rumored; doc says no
    "https://duck.ai",
]


def _status_headers() -> dict:
    return {
        "Host": "duckduckgo.com",
        "Accept": "text/event-stream",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://duckduckgo.com/",
        "User-Agent": DEFAULT_UA,
        "x-vqd-accept": "1",
        "DNT": "1",
        "Sec-GPC": "1",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


def _chat_headers(vqd_token: str) -> dict:
    return {
        "Host": "duckduckgo.com",
        "Accept": "text/event-stream",
        "Accept-Language": "en-US,en;q=0.5",
        "Content-Type": "application/json",
        "Referer": "https://duckduckgo.com/",
        "User-Agent": DEFAULT_UA,
        "x-vqd-4": vqd_token,
        "DNT": "1",
        "Sec-GPC": "1",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


def get_vqd() -> str:
    """Bootstrap a fresh x-vqd-4 token."""
    req = urllib.request.Request(STATUS_URL, headers=_status_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        token = resp.headers.get("x-vqd-4")
        if not token:
            raise RuntimeError(f"No x-vqd-4 in /status response (status {resp.status})")
        return token


def chat_once(model: str, messages: list[dict], vqd: str) -> tuple[str, str | None]:
    """One /chat call. Returns (full_text, fresh_token_from_headers)."""
    body = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    req = urllib.request.Request(CHAT_URL, data=body, headers=_chat_headers(vqd), method="POST")
    text_parts: list[str] = []
    error_info: str | None = None
    with urllib.request.urlopen(req, timeout=120) as resp:
        new_token = resp.headers.get("x-vqd-4")
        buffer = b""
        for chunk in resp:
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line.startswith(b"data: "):
                    continue
                payload = line[6:]
                if payload == b"[DONE]":
                    return "".join(text_parts), new_token
                try:
                    data = json.loads(payload.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                action = data.get("action")
                if action == "chunk":
                    msg = data.get("message", "")
                    if msg:
                        text_parts.append(msg)
                elif action == "error":
                    error_info = (
                        f"{data.get('type', '?')} (status={data.get('status', '?')}): "
                        f"{data.get('message', '')[:120]}"
                    )
        if error_info:
            raise RuntimeError(f"SSE error chunk: {error_info}")
        return "".join(text_parts), new_token


def discover_from_js() -> list[str]:
    """Grep duck.ai's JS bundle for hardcoded model IDs."""
    found: set[str] = set()
    for url in JS_BUNDLE_CANDIDATES:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": DEFAULT_UA, "Accept": "*/*"}, method="GET"
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read(2_000_000).decode("utf-8", errors="replace")
            if url.endswith("/models"):
                # If this endpoint exists, it's JSON
                try:
                    data = json.loads(html)
                    ids = [
                        m.get("id", m.get("model", ""))
                        for m in data.get("data", data if isinstance(data, list) else [])
                        if isinstance(m, dict)
                    ]
                    found.update(i for i in ids if i)
                except json.JSONDecodeError:
                    pass
                continue
            # HTML page: collect script srcs, then grep each bundle
            for src in re.findall(r'src="([^"]+\.js[^"]*)"', html):
                bundle_url = src if src.startswith("http") else f"https://duckduckgo.com{src}"
                try:
                    breq = urllib.request.Request(
                        bundle_url,
                        headers={"User-Agent": DEFAULT_UA, "Accept": "*/*"},
                        method="GET",
                    )
                    with urllib.request.urlopen(breq, timeout=30) as bresp:
                        js = bresp.read(8_000_000).decode("utf-8", errors="replace")
                    # Model-ID-ish literals: gpt-*, claude-*, o3-*, llama*,
                    # mixtral*, mistral*, *-haiku*, *Instruct*
                    for m in re.findall(
                        r'["\']([A-Za-z0-9_.\-/]*(?:gpt-|claude-|o3-|llama|mixtral|mistral|haiku|Instruct)[A-Za-z0-9_.\-/]*)["\']',
                        js,
                    ):
                        # filter obvious non-models (css classes, urls, words)
                        if (
                            len(m) > 6
                            and "/" in m
                            or re.match(r"^(gpt-|claude-|o3-|mixtral|mistral-small)", m)
                        ):
                            found.add(m)
                except (urllib.error.URLError, OSError):
                    continue
        except (urllib.error.URLError, OSError) as e:
            print(f"  [probe] {url} unreachable: {e}", file=sys.stderr)
    return sorted(found)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="Emit seed JSON block only")
    args = ap.parse_args()

    print("[1/3] Bootstrapping x-vqd-4 token via GET /duckchat/v1/status ...")
    try:
        vqd = get_vqd()
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(f"  token OK: {vqd[:20]}...")

    print("[2/3] Discovering models from duck.ai JS bundles ...")
    js_models = discover_from_js()
    for m in js_models:
        print(f"  bundle hit: {m}")

    all_candidates = list(dict.fromkeys(CANDIDATES + js_models))
    print(f"[3/3] Probing {len(all_candidates)} candidate model IDs ...")

    results: dict[str, dict] = {}
    for model in all_candidates:
        try:
            text, new_token = chat_once(model, [{"role": "user", "content": "Say OK."}], vqd)
            fresh = new_token or vqd
            if fresh != vqd:
                vqd = fresh  # rotate
            sample = text.strip()[:60]
            results[model] = {"status": "live", "sample": sample}
            print(f"  ✓ {model:55s} → {sample!r}")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:120]
            except Exception:
                pass
            # 401 → token expired mid-probe: re-bootstrap once and retry
            if e.code == 401:
                print(f"  ~ {model}: 401 token expired — re-bootstrapping")
                try:
                    vqd = get_vqd()
                    text, new_token = chat_once(
                        model, [{"role": "user", "content": "Say OK."}], vqd
                    )
                    vqd = (new_token or vqd).strip() or vqd
                    results[model] = {"status": "live", "sample": text.strip()[:60]}
                    print(f"  ✓ {model:55s} → {text.strip()[:60]!r}")
                    continue
                except Exception as e2:
                    e = e2 if isinstance(e2, urllib.error.HTTPError) else e
                    body = f"{type(e2).__name__}: {e2}"[:120]
            results[model] = {"status": f"http-{e.code}", "detail": body}
            print(f"  ✗ {model:55s} → HTTP {e.code} {body[:60]}")
        except Exception as e:
            results[model] = {"status": "error", "detail": str(e)[:120]}
            print(f"  ✗ {model:55s} → {type(e).__name__}: {str(e)[:60]}")
        time.sleep(1.0)  # be polite to DDG's anti-abuse heuristics

    live = sorted(m for m, r in results.items() if r["status"] == "live")
    print(f"\n=== RESULT: {len(live)}/{len(all_candidates)} models live ===")
    if args.json:
        seed = {m: {"default_temperature": 0.7, "default_max_tokens": 4096} for m in live}
        print(json.dumps(seed, indent=2))
    else:
        for m in live:
            print(f"  {m}")
    return 0 if live else 1


if __name__ == "__main__":
    sys.exit(main())
