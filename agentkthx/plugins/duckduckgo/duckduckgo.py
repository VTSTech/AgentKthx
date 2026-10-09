"""
⚛️ AgentKthx — DuckDuckGo AI Chat Backend
Backend implementation for the DuckDuckGo AI Chat (duck.ai) API.

DuckDuckGo AI Chat is the keyless, anonymous, zero-cost LLM surface:
no API key, no signup, no quota. Current upstream lineup (verified
Oct 2026 against the live duck.ai dropdown and the maintained Go
client benoitpetit/duckduckgo-chat-cli): OpenAI GPT-6 Luna / GPT-5.6
Luna / GPT-5.4 Nano / GPT-5.4 Mini, Anthropic Claude Haiku 4.5,
Mistral Small 4, and the Tinfoil-hosted open-weight pair
gpt-oss-120b / Gemma 4 31B — all proxied through DuckDuckGo's
privacy layer, which strips your IP before forwarding upstream.

This backend is DELIBERATELY unlike every other AgentKthx cloud
backend. The protocol is NOT OpenAI Chat-Completions — it is
DuckDuckGo's own /duckchat/v1/* wire format, and as of the
2026-10 protocol rotation it is CHALLENGE-BASED:

  Boot + per-request proof:
  - GET  /duckchat/v1/status (header ``x-vqd-accept: 1``)
    → response header ``x-vqd-hash-1`` = base64 obfuscated JS
    challenge (obfuscator.io-style, re-randomized per issue)
  - The challenge is EXECUTED against a clean-browser environment
    by the bundled Node solver (``ddg_vqd.js``) → raw solution
    {server_hashes, client_hashes, signals, meta}
  - Post-processing mirrors the duck.ai page bundle:
    client_hashes[i] = base64(SHA256(String(raw))), meta gains
    origin/stack/duration → base64(JSON) = the ``X-Vqd-Hash-1``
    REQUEST header (the proof)
  - POST /duckchat/v1/chat with that proof; the RESPONSE header
    ``x-vqd-hash-1`` carries the NEXT challenge (per-turn rotation)

  The retired ``x-vqd-4`` opaque token is GONE — /status no longer
  returns it, and requests that send neither proof nor token fail.
  This is why the pre-rotation scaffold broke (protocol drift, not
  header shape: proven by a curl UA/Origin/casing/HTTP-version
  matrix — every variant got the challenge).

  Chat body (current shape, mirrors the live frontend):
    {model, messages, canUseTools, canUseApproxLocation,
     reasoningEffort, durableStream:{messageId, conversationId,
     publicKey <client-minted RSA-2048 JWK>}}
  ``durableStream.publicKey`` is generated client-side (Node crypto
  — Python stdlib has no RSA); the web client persists its pair in
  localStorage under ``duckaiStreamsTestCredentials``. We mint a
  fresh pair per backend instance (= per conversation) via the
  bundled ``ddg_durable.js``.

  Response: SSE with ROLE-BASED events (the old action:"chunk"
  grammar is gone):
    data: {"role":"assistant","message":"…"}      text deltas
    data: {"role":"source","source":{url,title}}  web citations
    data: {"role":"tool-invocation",…}            native tool frames
    data: {"role":"ui-component",name:…}          UI components
    data: [DONE] / [PING] / [CHAT_TITLE:…]
  The legacy action grammar is still parsed as a fallback — DDG
  has flipped event shapes before without notice.

Protocol constraints honored here:
  - No ``system`` role — DDG strips it, and live testing (2026-10-09,
    claude-haiku-4-5 via duck.ai) showed the upstream models read a
    forwarded harness/system prompt as a JAILBREAK ATTEMPT: they refuse,
    lecture about social engineering, and break the session.
    ``_strip_system_messages`` therefore DROPS system content instead of
    smuggling it into user turns — the AgentKthx system prompt (ReAct
    scaffolding included) never reaches DDG; the backend transmits the
    user/assistant conversation only.
  - No native function calling from AgentKthx yet — the wire now
    HAS a native tools surface (canUseTools + metadata.toolChoice
    with WebSearch / GenerateImage / NewsSearch / VideosSearch /
    LocalSearch / WeatherForecast), but it is undocumented and
    rotates independently; ``test_tool_support()`` stays REACT and
    ``canUseTools`` ships false until that surface stabilizes.
  - No sampling params — temperature / max_tokens / top_p / stop /
    seed are accepted at the interface and silently dropped.
    ``reasoningEffort`` is set per-model ("low" for the Tinfoil
    open-weight pair, "none" otherwise) exactly like the frontend.
  - Always-streaming — responses arrive as SSE; ``generate()``
    buffers the stream, ``generate_stream()`` yields text deltas
    as they arrive (FEAT-10 — CLOSED by this rotation).
  - Node is a runtime dependency for the challenge solver and the
    durableStream keypair (subprocess, no npm packages). Without
    node on PATH this backend fails fast with a clear message; the
    same fail-fast applies when the bundled .js helpers are missing
    from the installed package (pip builds predating the package-data
    fix dropped them — the remediation says how to fix the install).
  - Anti-bot contract v3 (2026-10-09 hardening, learned from the
    429 storm, the 418 teapot escalation, and the maintained Go
    client): minimum cookie set (``5=1; dcm=3; dcs=1``) + Chromium
    client hints on every request; HTTP 418 (the teapot — proof
    rejected or the challenge ladder escalated; observed 2026-10-09
    on the strict routes gpt-6-luna, claude-haiku-4-5,
    tinfoil/gemma4-31b) joins 401/SSE-ERR_CHALLENGE as a retryable
    proof failure: up to ``_CHALLENGE_RETRIES`` re-solves,
    preferring the challenge carried on the 418's own
    ``x-vqd-hash-1`` response header (the ladder), falling back to
    /status, and escalating the proof's reported solve duration to
    a plausible floor (strict routes flag sub-100ms solves); 429
    surfaces the server's Retry-After when present; /chat POSTs
    are paced to DUCKDUCKGO_MIN_INTERVAL (default 3s) so agentic
    loops don't rate-limit themselves.
  - Proof modes: ``synth`` (default — /status challenge solved by
    the bundled Node helper) or DUCKDUCKGO_PROOF_MODE=capture (a
    real headless-Chromium session loads duck.ai, the PAGE builds
    its own proof, and the outgoing /chat request is intercepted +
    aborted before it consumes quota — the exact design the
    maintained Go client ships; needs a local Chrome/Chromium and
    Node >= 22).

Model catalog (wire IDs — the 2025-era IDs now 404 with
ERR_MODEL_UNAVAILABLE; verified 2026-10-09):

  - gpt-6-luna            (DDG's default)
  - gpt-5.6-luna
  - gpt-5.4-nano
  - gpt-5.4-mini
  - claude-haiku-4-5
  - mistral-small-2603    (display name "Mistral Small 4")
  - tinfoil/gpt-oss-120b  (reasoningEffort low)
  - tinfoil/gemma4-31b    (reasoningEffort low)

Legacy aliases resolve forward (gpt-4o-mini → gpt-5.6-luna,
claude-3-haiku → claude-haiku-4-5, llama → tinfoil/gpt-oss-120b,
mixtral → mistral-small-2603, o3-mini/o4mini → gpt-5.4-mini) so
existing sessions and scripts keep working across the rotation.

Usage:
    AGENTKTHX_BACKEND=duckduckgo agentkthx chat -m gpt-6-luna
    agentkthx models --backend duckduckgo

See docs/api/DUCKDUCKGO_API_TECHNICAL_REFERENCE.md for full details.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from agentkthx import model_cache
from agentkthx.backends.base import BaseBackend
from agentkthx.config import DUCKDUCKGO_BASE_URL, DUCKDUCKGO_DEFAULT_MODEL, DUCKDUCKGO_USER_AGENT
from agentkthx.core.types import BackendType, ThinkingSupport, ToolSupportLevel
from agentkthx.model_cache import load_seed_catalog

#: Default browser User-Agent — DDG 403s non-browser UAs. Must match
#: the UA the challenge solver emulates (DDG_SOLVE_UA is injected from
#: this value at solve time). Overridable via DUCKDUCKGO_USER_AGENT.
_DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)

#: Bundled Node helpers (challenge solver + durableStream keygen +
#: optional browser proof-capture). Shipped inside the plugin package
#: so the backend is self-contained — pyproject package-data MUST
#: include plugins/*/*.js or pip installs silently drop them (live
#: finding 2026-10-09: the site-packages install had no .js at all).
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_SOLVER_JS = os.path.join(_PLUGIN_DIR, "ddg_vqd.js")
_DURABLE_JS = os.path.join(_PLUGIN_DIR, "ddg_durable.js")
_CAPTURE_JS = os.path.join(_PLUGIN_DIR, "ddg_capture.js")

#: Minimum cookie contract (mirrors benoitpetit/duckduckgo-chat-cli,
#: which sets exactly these three on every request). Cheap to send,
#: plausibly checked by the anti-bot layer on /chat.
_MIN_COOKIES = "5=1; dcm=3; dcs=1"

#: Client hints consistent with the default UA (Chrome 136 / Linux).
#: The challenge's client_hashes[0] binds the proof to the UA header;
#: these hints ride along the way a real Chromium sends them.
_SEC_CH_UA = '"Chromium";v="136", "Not.A/Brand";v="99", "Google Chrome";v="136"'
_SEC_CH_UA_PLATFORM = '"Linux"'

#: Proof duration plausibility floor (ms). The Node solver finishes
#: a challenge in 60-90ms — faster than any real browser could run
#: the obfuscated bundle. The strict model routes (2026-10-09: the
#: ones answering HTTP 418 ERR_CHALLENGE) plausibility-check
#: ``meta.duration``; 418 retries re-solve with this floor applied
#: (the first attempt stays honest).
_PROOF_DURATION_FLOOR_MS = 250

#: Pause between 418 re-solve rounds — the teapot ladder is climbed
#: politely, not hammered.
_CHALLENGE_RETRY_SLEEP_S = 1.0

#: Proof acquisition mode. "synth" (default) = fetch challenge ->
#: Node solve -> build header. "capture" = make a real headless
#: Chromium load duck.ai and lift the proof off its aborted /chat
#: request (the approach the maintained Go client shipped after the
#: 2026-10 hardening — immune to proof-validation tightening, needs
#: a local Chrome/Chromium + Node >= 22). Set DUCKDUCKGO_PROOF_MODE.
_PROOF_MODE = (os.environ.get("DUCKDUCKGO_PROOF_MODE") or "synth").strip().lower()

#: Frontend metadata fallbacks (user capture, 2026-10-08 build).
#: Best-effort refreshed from the homepage HTML at runtime — the
#: serp version rotates with deployments.
_FE_VERSION_FALLBACK = "serp_20261008_165829_ET-12db35fbec1a8e8189c691f90debc97b169e1c5a"
_ENTRY_JS_FALLBACK = "entry.duckai.9c8d02dfc0e8ac34875b.js"
_VENDORS_JS_FALLBACK = "entry.vendors.809c2cd1740bd8686f2.js"

#: Wire model catalog — order matters (first = DDG's own default).
#: ``effort`` is the reasoningEffort the frontend sends per model.
_WIRE_MODELS: list[dict] = [
    {"id": "gpt-6-luna", "name": "GPT-6 Luna", "effort": "none"},
    {"id": "gpt-5.6-luna", "name": "GPT-5.6 Luna", "effort": "none"},
    {"id": "gpt-5.4-nano", "name": "GPT-5.4 Nano", "effort": "none"},
    {"id": "gpt-5.4-mini", "name": "GPT-5.4 Mini", "effort": "none"},
    {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5", "effort": "none"},
    {"id": "mistral-small-2603", "name": "Mistral Small 4", "effort": "none"},
    {"id": "tinfoil/gpt-oss-120b", "name": "GPT OSS 120B", "effort": "low"},
    {"id": "tinfoil/gemma4-31b", "name": "Gemma 4 31B", "effort": "low"},
]

#: Short-alias → canonical wire-ID map. Legacy 2025-era wire IDs are
#: included (forward-compatible: the catalog rotated underneath them
#: and they now 404 with ERR_MODEL_UNAVAILABLE). Mirrors the compat
#: map in benoitpetit/duckduckgo-chat-cli. Keys are matched
#: CASE-INSENSITIVELY (_resolve_model lowercases the input first), so
#: they are written lowercase — including the historical mixed-case
#: wire IDs (meta-llama/Llama-3.3-70B-Instruct-Turbo et al).
_MODEL_ALIASES: dict[str, str] = {
    # legacy wire IDs (2025 era)
    "gpt-4o-mini": "gpt-5.6-luna",
    "o3-mini": "gpt-5.4-mini",
    "o4mini": "gpt-5.4-mini",
    "claude-3-haiku": "claude-haiku-4-5",
    "claude-3-haiku-20240307": "claude-haiku-4-5",
    "mistralai/mistral-small-24b-instruct-2501": "mistral-small-2603",
    "meta-llama/llama-3.3-70b-instruct-turbo": "tinfoil/gpt-oss-120b",
    # short aliases (stable across rotations)
    "llama": "tinfoil/gpt-oss-120b",
    "mixtral": "mistral-small-2603",
    "gpt": "gpt-6-luna",
    "claude": "claude-haiku-4-5",
    "mistral": "mistral-small-2603",
    "gemma": "tinfoil/gemma4-31b",
    "oss": "tinfoil/gpt-oss-120b",
    "nano": "gpt-5.4-nano",
    "mini": "gpt-5.4-mini",
}

#: Static catalog from agentkthx/data/model_seed.json — the 8
#: client-verified DDG chat models with context metadata. The live
#: catalog is available via ``fetch_capabilities()`` (the
#: /duckchat/v1/capabilities endpoint); the seed is the offline
#: fallback for `agentkthx models`.
DUCKDUCKGO_MODELS: dict[str, dict] = load_seed_catalog("duckduckgo")

#: Default model when none specified. gpt-6-luna is DuckDuckGo's own
#: default (the natural zero-config pick); override via
#: DUCKDUCKGO_DEFAULT_MODEL.
DUCKDUCKGO_DEFAULT_MODEL_STR = DUCKDUCKGO_DEFAULT_MODEL or "gpt-6-luna"


def _resolve_model(model: str) -> str:
    """Alias-tolerant model resolution → canonical wire ID.

    Resolution order: exact wire-ID match (case-insensitive) →
    legacy/short alias → pass through unchanged (the caller surfaces
    the upstream ERR_MODEL_UNAVAILABLE with the catalog-drift hint).
    """
    key = (model or "").strip().lower()
    for entry in _WIRE_MODELS:
        if entry["id"] == key:
            return entry["id"]
    return _MODEL_ALIASES.get(key, model)


def _estimate_tokens(text: str) -> int:
    """~4 chars/token estimate (the DDG protocol reports no usage)."""
    return max(1, len(text or "") // 4)


def _rand_id() -> str:
    """32-hex random ID (messageId / conversationId / journey shape)."""
    return "%032x" % random.randrange(16**32)


class _ChallengeError(Exception):
    """SSE ERR_CHALLENGE — retryable by re-solving a fresh proof."""


class _ConversationLimitError(Exception):
    """ERR_CONVERSATION_LIMIT / [LIMIT_CONVERSATION] — conv is full."""


class _ModelUnavailableError(Exception):
    """HTTP 404 ERR_MODEL_UNAVAILABLE — the catalog rotated again."""


def _classify_sse_payload(payload: bytes) -> dict | None:
    """One ``data:`` payload → event dict (None = ignorable noise).

    Handles the CURRENT role-based grammar, the legacy action-based
    grammar, and the bracketed markers. Kept module-level so tests
    can exercise it without a backend instance.
    """
    if payload == b"[DONE]":
        return {"kind": "done"}
    if payload == b"[PING]":
        return {"kind": "ping"}
    if payload == b"[LIMIT_CONVERSATION]":
        return {"kind": "limit"}
    if payload.startswith(b"[CHAT_TITLE:") and payload.endswith(b"]"):
        return {"kind": "title", "text": payload[12:-1].decode("utf-8", "replace").strip()}
    try:
        data = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    role = data.get("role")
    action = data.get("action")

    if role == "assistant":
        if data.get("message"):
            return {"kind": "text", "text": data["message"]}
        return None  # status/state-only assistant frame
    if role == "source":
        src = data.get("source") or {}
        if src.get("url"):
            return {"kind": "source", "url": src.get("url", ""), "title": src.get("title", "")}
        return None
    if role == "tool-invocation":
        return {"kind": "tool", "tool": data.get("toolName", ""), "state": data.get("state", "")}
    if role == "ui-component":
        return {"kind": "ui", "name": data.get("name", "")}
    if role == "error" or action == "error":
        return {"kind": "error", "type": data.get("type", ""), "status": data.get("status")}
    if action == "chunk":  # legacy 2025 grammar — kept as fallback
        if data.get("message"):
            return {"kind": "text", "text": data["message"]}
        return None
    return None


class DuckDuckGoBackend(BaseBackend):
    """duck.ai backend — challenge-proof protocol, always-SSE.

    What this class owns:
      - the x-vqd-hash-1 proof lifecycle (fetch challenge → Node
        solve → build header → per-turn rotation via the response
        header)
      - the durableStream RSA keypair (per-instance = per-conversation)
      - the current chat body shape (canUseTools / reasoningEffort /
        durableStream)
      - role-based SSE parsing (with legacy-action fallback)
      - buffered ``generate()`` AND incremental ``generate_stream()``
      - the seed-backed offline catalog + live ``fetch_capabilities()``

    What it deliberately does NOT do:
      - native tools (canUseTools=false shipped until the wire
        surface stabilizes — ReAct remains the tool path)
      - sampling params (dropped — upstream defaults apply)
    """

    #: Catalog cache TTL for list_models() (house: 1h).
    _MODEL_CACHE_TTL_SECONDS = 3600

    #: Minimum wall-clock gap between /chat POSTs (per-IP courtesy —
    #: DDG's anonymous limit is strict and undocumented, and agentic
    #: loops fire turns back-to-back). Override or disable via
    #: DUCKDUCKGO_MIN_INTERVAL (seconds; 0 disables). Tests zero the
    #: class attribute on the fixture.
    _MIN_CHAT_INTERVAL_S = 3.0
    _last_chat_monotonic: float = 0.0
    #: 418 ERR_CHALLENGE re-solve budget (teapot ladder climbing).
    _CHALLENGE_RETRIES: int = 3
    #: model_cache namespace (unchanged — same key as pre-rotation).
    MODEL_CACHE_KEY = "duckduckgo"

    #: Cloud-hosted — BaseBackend deliberately defaults direct
    #: subclasses to False (R06.57 MAINT-05) so unknown backends are
    #: treated as local; DDG is remote, so override to True or the
    #: CLI misconfigures streaming defaults / models-table columns.
    is_cloud: bool = True

    def __init__(
        self,
        config=None,
        base_url: str | None = None,
        api_mode: object | None = None,
        user_agent: str | None = None,
        **kwargs,
    ):
        super().__init__(config=config, base_url=base_url, api_mode=api_mode)

        #: Browser UA (DDG 403s non-browser UAs). The challenge
        #: solver MUST see the same UA (it is injected via
        #: DDG_SOLVE_UA at solve time — a UA mismatch between the
        #: solved probes and the request headers invalidates the
        #: proof). Precedence: kwarg > DUCKDUCKGO_USER_AGENT > Chrome 136.
        self._user_agent = user_agent or DUCKDUCKGO_USER_AGENT or _DEFAULT_UA

        #: /chat pacing override — DUCKDUCKGO_MIN_INTERVAL (seconds,
        #: 0 disables). Read here (not config.py) to keep the plugin
        #: self-contained; garbage values keep the class default.
        try:
            self._MIN_CHAT_INTERVAL_S = float(
                os.environ.get("DUCKDUCKGO_MIN_INTERVAL") or self._MIN_CHAT_INTERVAL_S
            )
        except ValueError:
            pass

        #: Proof state — the NEXT challenge (base64), carried on the
        #: previous /chat response header. When None, _fetch_challenge
        #: bootstraps from /status.
        self._next_challenge_b64: str | None = None

        #: durableStream state — minted lazily on first chat; one
        #: keypair + conversationId per backend instance.
        self._durable: dict | None = None

        #: Frontend metadata (serp version + bundle names), scraped
        #: best-effort from GET / and cached 10 minutes.
        self._fe_meta: dict = {}
        self._fe_meta_ts: float = 0.0

        #: node availability (checked once, cached; None = unknown).
        self._node_ok: bool | None = None

        self._model_cache: list[dict] | None = None
        self._model_cache_ts: float = 0.0

    # ─────────────────────────────────────────────────────────────────────
    # Provider identity
    # ─────────────────────────────────────────────────────────────────────

    @property
    def backend_type(self) -> BackendType:
        """DuckDuckGo's dedicated BackendType enum value."""
        return BackendType.DUCKDUCKGO

    @property
    def base_url(self) -> str:
        """API root — https://duck.ai (protocol lives under /duckchat/v1)."""
        return self._base_url or DUCKDUCKGO_BASE_URL

    # ─────────────────────────────────────────────────────────────────────
    # Node helpers — the challenge solver + keygen subprocesses
    # ─────────────────────────────────────────────────────────────────────

    def _node_available(self) -> bool:
        """node on PATH (checked once per instance, cached)."""
        if self._node_ok is None:
            self._node_ok = shutil.which("node") is not None
        return self._node_ok

    def _require_node(self, what: str) -> None:
        """Fail fast with the remediation when node is missing."""
        if not self._node_available():
            raise RuntimeError(
                f"DuckDuckGo backend needs Node.js for {what} (the "
                "x-vqd-hash-1 challenge solver / durableStream keygen "
                "ship as bundled .js helpers). Install node >= 18 and "
                "make sure it is on PATH."
            )

    def _require_helper(self, path: str, what: str) -> None:
        """Fail fast with the reinstall remediation when a bundled
        .js helper is missing from the installed package.

        Live finding 2026-10-09: pip builds predating the package-data
        fix ship the plugin's .py files but silently drop the .js
        helpers — every generate() then dies with a cryptic Node
        MODULE_NOT_FOUND naming a site-packages path. This check
        turns that into the actual fix.
        """
        self._require_node(what)
        if not os.path.isfile(path):
            raise RuntimeError(
                f"DuckDuckGo backend helper {os.path.basename(path)} is "
                f"missing from the installed plugin directory "
                f"({os.path.dirname(path)}). The installed package was "
                "built without the plugin's .js helpers — fix with "
                "`pip install -e /path/to/AgentKthx` (editable install) "
                "or copy ddg_vqd.js + ddg_durable.js (+ ddg_capture.js "
                "if you use capture mode) from the repo's "
                "agentkthx/plugins/duckduckgo/ into that directory."
            )

    def _solve_challenge(self, challenge_b64: str) -> dict:
        """Run the bundled Node solver → raw solution dict.

        The solver emulates a clean browser (navigator, DOM probes,
        sandbox iframes, native-code fingerprints) and executes the
        obfuscated challenge JS against it. DDG_SOLVE_UA is set to
        THIS backend's UA — a mismatch invalidates the proof.
        """
        self._require_node("challenge solving")
        env = dict(os.environ)
        env["DDG_SOLVE_UA"] = self._user_agent
        proc = subprocess.run(
            ["node", _SOLVER_JS],
            input=challenge_b64.encode("utf-8"),
            capture_output=True,
            timeout=45,
            env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "DuckDuckGo challenge solver failed (rc="
                f"{proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace').strip()[:400]}"
            )
        return json.loads(proc.stdout.decode("utf-8"))

    def _mint_durable(self) -> dict:
        """Mint the durableStream RSA-2048 keypair (Node crypto).

        Python stdlib has no RSA — the bundled ddg_durable.js does
        generateKeyPairSync('rsa', 2048) and emits the exact JWK
        shape the frontend sends. One pair per backend instance
        (= per conversation); messageId is fresh per request.
        """
        self._require_node("durableStream keygen")
        proc = subprocess.run(
            ["node", _DURABLE_JS],
            capture_output=True,
            timeout=20,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "DuckDuckGo durableStream keygen failed (rc="
                f"{proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace').strip()[:400]}"
            )
        keys = json.loads(proc.stdout.decode("utf-8"))
        return {
            "conversation_id": _rand_id(),
            "public_key": keys["publicJwk"],
        }

    # ─────────────────────────────────────────────────────────────────────
    # Proof lifecycle — fetch challenge → solve → build header
    # ─────────────────────────────────────────────────────────────────────

    def _fetch_challenge(self) -> str:
        """Get a challenge blob: response-carried, else /status.

        The /chat response header ``x-vqd-hash-1`` carries the next
        challenge (the protocol's intended rotation). When that is
        exhausted (fresh conversation, prior error), bootstrap via
        GET /status with ``x-vqd-accept: 1``.
        """
        if self._next_challenge_b64:
            chal = self._next_challenge_b64
            self._next_challenge_b64 = None
            return chal

        req = urllib.request.Request(
            f"{self.base_url}/duckchat/v1/status",
            headers=self._build_status_headers(),
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            challenge = resp.headers.get("x-vqd-hash-1")
            resp.read()  # drain (body is small JSON)
            if not challenge:
                raise RuntimeError(
                    f"DuckDuckGo /status returned no x-vqd-hash-1 "
                    f"challenge (status {resp.status}) — the protocol "
                    "rotated again; see "
                    "docs/api/DUCKDUCKGO_API_TECHNICAL_REFERENCE.md"
                )
            return challenge

    def _synth_stack(self) -> str:
        """A plausible duck.ai bundle stack (shape from a real capture)."""
        entry = self._fe_meta.get("entry_js", _ENTRY_JS_FALLBACK)
        vendors = self._fe_meta.get("vendors_js", _VENDORS_JS_FALLBACK)
        r = lambda a, b: random.randint(a, b)  # noqa: E731
        return (
            f"l@https://duck.ai/dist/duckai-dist/{entry}:2:{r(1900000, 2050000)}\n"
            f"async*30012/h/y<@https://duck.ai/dist/duckai-dist/{entry}:2:{r(1700000, 1800000)}\n"
            f"onSuccess@https://duck.ai/dist/duckai-dist/{entry}:2:{r(1600000, 1700000)}\n"
            f"32985/ne/pE<@https://duck.ai/dist/duckai-dist/{vendors}:8:{r(1600000, 1650000)}\n"
            f"async*32985/ne/<@https://duck.ai/dist/duckai-dist/{vendors}:8:{r(1630000, 1640000)}\n"
            f"... (25 more frames omitted)"
        )

    def _build_proof_header(self, raw: dict, elapsed_ms: int) -> str:
        """Raw solver output → the X-Vqd-Hash-1 request header value.

        Mirrors the duck.ai page bundle's post-processing:
        client_hashes are base64(SHA256(String(value))) of the raw
        probe results; server_hashes pass through UNTOUCHED (they are
        opaque server nonces); meta gains origin/stack/duration.
        """
        solution = {
            "server_hashes": raw.get("server_hashes", []),
            "client_hashes": [
                base64.b64encode(hashlib.sha256(str(v).encode("utf-8")).digest()).decode()
                for v in raw.get("client_hashes", [])
            ],
            "signals": raw.get("signals", {}),
            "meta": {
                "v": raw.get("meta", {}).get("v", "4"),
                "challenge_id": raw.get("meta", {}).get("challenge_id", ""),
                "timestamp": raw.get("meta", {}).get("timestamp", ""),
                "debug": raw.get("meta", {}).get("debug", ""),
                "origin": "https://duck.ai",
                "stack": self._synth_stack(),
                "duration": str(max(1, int(elapsed_ms))),
            },
        }
        return base64.b64encode(json.dumps(solution, separators=(",", ":")).encode("utf-8")).decode(
            "utf-8"
        )

    def _acquire_proof(self, min_duration_ms: int = 0) -> str:
        """Fresh solved X-Vqd-Hash-1 (fetch → solve → build).

        ``min_duration_ms`` pads the reported solve time up to a
        plausible floor: the Node solver finishes in 60-90ms, faster
        than any real browser could execute the obfuscated bundle,
        and the strict model routes (the 2026-10-09 418 crowd)
        plausibility-check ``meta.duration``. The first attempt stays
        honest (floor 0); 418 retries escalate to the floor.
        """
        t0 = time.monotonic()
        raw = self._solve_challenge(self._fetch_challenge())
        elapsed_ms = (time.monotonic() - t0) * 1000
        if min_duration_ms and elapsed_ms < min_duration_ms:
            elapsed_ms = min_duration_ms + random.randint(0, 150)
        return self._build_proof_header(raw, elapsed_ms)

    # ─────────────────────────────────────────────────────────────────────
    # Headers (anti-bot contract: browser UA + Referer + SSE Accept)
    # ─────────────────────────────────────────────────────────────────────

    def _build_status_headers(self) -> dict:
        """Headers for the /status challenge bootstrap.

        ``x-vqd-accept: 1`` is the opt-in that makes /status issue a
        challenge instead of the bare status JSON. ``x-ddg-journey-id``
        is a per-request 32-hex session marker (shape from the live
        frontend capture).
        """
        return {
            "User-Agent": self._user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{self.base_url}/",
            "Cookie": _MIN_COOKIES,
            "sec-ch-ua": _SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": _SEC_CH_UA_PLATFORM,
            "Cache-Control": "no-store",
            "x-vqd-accept": "1",
            "x-ddg-journey-id": _rand_id(),
            "Sec-GPC": "1",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    def _build_chat_headers(self, proof: str) -> dict:
        """Headers for POST /chat — proof + browser contract.

        The proof header carries the SOLVED challenge; x-fe-version /
        x-fe-signals mirror the frontend telemetry (version from the
        scraped serp build; signals = base64 JSON event log with the
        onboarding/startNewChat_free events the real client sends).
        """
        return {
            "User-Agent": self._user_agent,
            "Accept": "text/event-stream",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{self.base_url}/",
            "Cookie": _MIN_COOKIES,
            "sec-ch-ua": _SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": _SEC_CH_UA_PLATFORM,
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "x-fe-version": self._fe_meta.get("fe_version", _FE_VERSION_FALLBACK),
            "x-fe-signals": self._fe_signals(),
            "X-Vqd-Hash-1": proof,
            "x-ddg-journey-id": _rand_id(),
            "Cache-Control": "no-store",
            "Sec-GPC": "1",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    def _fe_signals(self) -> str:
        """Synthesized frontend telemetry (shape from a live capture).

        base64(JSON{start, events[action + startNewChat_free], end})
        with plausible millisecond deltas — the real client's
        engagement proof. Accepted by the server alongside the solved
        challenge (verified end-to-end pre-integration).
        """
        now = int(time.time() * 1000)
        start = now - random.randint(5000, 60000)
        payload = {
            "start": start,
            "events": [
                {"name": "action", "delta": random.randint(300, 3000), "trusted": True},
                {"name": "startNewChat_free", "delta": random.randint(3100, 6000)},
            ],
            "end": start + random.randint(6100, 9000),
        }
        return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode(
            "utf-8"
        )

    def _scrape_fe_meta(self, max_age_s: float = 600.0) -> dict:
        """Pull serp version + bundle names from GET / (cached, best-effort).

        The x-fe-version string rotates with frontend deployments; a
        stale one is currently tolerated by the server but scraping
        keeps us honest (and refreshes the stack-synthesis bundle
        names). Failures fall back to the captured constants silently.
        """
        if self._fe_meta and (time.monotonic() - self._fe_meta_ts) < max_age_s:
            return self._fe_meta
        try:
            req = urllib.request.Request(
                f"{self.base_url}/",
                headers={"User-Agent": self._user_agent, "Accept": "text/html,*/*"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read(2_000_000)
            for key, pattern in (
                ("fe_version", rb"serp_\d{8}_\d{6}_[A-Za-z0-9-]+"),
                ("entry_js", rb"entry\.duckai\.[0-9a-f]+\.js"),
                ("vendors_js", rb"entry\.vendors\.[0-9a-f]+\.js"),
            ):
                m = re.search(pattern, html)
                if m:
                    self._fe_meta[key] = m.group(0).decode("ascii")
            self._fe_meta_ts = time.monotonic()
        except (urllib.error.URLError, OSError, ValueError):
            pass  # best-effort — fallbacks apply
        return self._fe_meta

    # ─────────────────────────────────────────────────────────────────────
    # Body builder — the current chat wire shape
    # ─────────────────────────────────────────────────────────────────────

    def _build_chat_body(self, wire_model: str, convo: list[dict]) -> bytes:
        """POST /chat body: model + messages + protocol fields.

        ``canUseTools`` false + no ``metadata`` (native tools are a
        rotating undocumented surface — ReAct stays the tool path).
        ``canUseApproxLocation`` null (frontend sends null when no
        location consent). ``reasoningEffort`` per-model like the
        frontend ("low" for the Tinfoil open-weight pair). The
        ``durableStream`` publicKey is the per-conversation RSA JWK.
        """
        if self._durable is None:
            self._durable = self._mint_durable()
        effort = next(
            (m["effort"] for m in _WIRE_MODELS if m["id"] == wire_model),
            "none",
        )
        body = {
            "model": wire_model,
            "messages": convo,
            "canUseTools": False,
            "canUseApproxLocation": None,
            "reasoningEffort": effort,
            "durableStream": {
                "messageId": _rand_id(),
                "conversationId": self._durable["conversation_id"],
                "publicKey": self._durable["public_key"],
            },
        }
        return json.dumps(body, separators=(",", ":")).encode("utf-8")

    # ─────────────────────────────────────────────────────────────────────
    # Message shaping (the protocol strips system + tool fields;
    # the harness system prompt is NEVER forwarded — see
    # _strip_system_messages)
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _coerce_content(content) -> str:
        """Best-effort content → plain text (str | list-of-parts)."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("text"):
                    parts.append(str(part["text"]))
                elif isinstance(part, str):
                    parts.append(part)
            return "\n".join(parts)
        return "" if content is None else str(content)

    def _strip_system_messages(self, messages: list[dict]) -> list[dict]:
        """DDG never sees the system prompt — system messages are DROPPED.

        Two reasons stack here:

        1. The wire has no ``system`` role — DDG strips it server-side.
        2. Live testing (2026-10-09, claude-haiku-4-5 via duck.ai) showed
           that folding harness/system content into the first user turn
           reads as a jailbreak attempt to the upstream models: they
           refuse, lecture about social engineering, and derail the
           session. Decision (VTSTech, R07.30): the AgentKthx system
           prompt — ReAct scaffolding included — is never transmitted
           to DDG. The backend forwards the user/assistant conversation
           only; tool/function outputs still fold into user turns
           (DDG rejects foreign roles, and the ReAct loop's tool
           results depend on that folding).

        Preserves order for user/assistant turns. Returns [] when
        nothing sendable remains (system-only conversation) — the
        generate() paths refuse that with ``ValueError``.
        """
        convo: list[dict] = []

        def _content(m: dict) -> str:
            return self._coerce_content(m.get("content", ""))

        for msg in messages:
            role = (msg.get("role") or "user").lower()
            if role == "system":
                # Dropped, never forwarded (see docstring).
                continue
            if role not in ("user", "assistant"):
                # tool / function results — DDG only understands
                # user/assistant; fold foreign roles into user (the
                # ReAct loop's tool outputs ride as user turns).
                role = "user"
            content = _content(msg)
            if content or role == "user":
                convo.append({"role": role, "content": content})

        if convo and convo[0].get("role") != "user":
            convo.insert(0, {"role": "user", "content": "Hello"})
        return convo

    # ─────────────────────────────────────────────────────────────────────
    # Generation — buffered (generate) + incremental (generate_stream)
    # ─────────────────────────────────────────────────────────────────────

    def _pace_chat(self) -> None:
        """Enforce the minimum /chat POST interval (anti-429 courtesy).

        DDG's anonymous per-IP limit is strict and undocumented, and
        the 2026-10-09 429 storm showed back-to-back ReAct turns can
        trip it single-handedly. The timestamp is CLASS-level — one
        pace across every backend instance in this process. No-op
        when ``_MIN_CHAT_INTERVAL_S`` <= 0 (tests zero it on the
        fixture; DUCKDUCKGO_MIN_INTERVAL=0 disables at runtime).
        """
        interval = self._MIN_CHAT_INTERVAL_S
        if not interval or interval <= 0:
            return
        now = time.monotonic()
        wait = interval - (now - DuckDuckGoBackend._last_chat_monotonic)
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        DuckDuckGoBackend._last_chat_monotonic = now

    def _log_418(self, attempt: int, detail: str, ladder: str | None) -> None:
        """AGENTKTHX_DEBUG trace for one 418 teapot round."""
        if not os.environ.get("AGENTKTHX_DEBUG"):
            return
        try:
            parsed = json.loads(detail)
            cd = parsed.get("cd") or {}
            info = (
                f"type={parsed.get('type')} round={cd.get('i')} "
                f"override={parsed.get('overrideCode')}"
            )
        except (json.JSONDecodeError, AttributeError):
            info = detail[:120]
        source = "response-header ladder challenge" if ladder else "/status re-bootstrap"
        print(
            f"  [duckduckgo] HTTP 418 ({info}) — re-solving from {source} "
            f"[attempt {attempt}/{self._CHALLENGE_RETRIES}]"
        )

    def generate(
        self,
        model: str,
        messages: list[dict],
        tools: list | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        think: bool | None = None,
        **kwargs,
    ) -> dict:
        """Generate a response via POST /duckchat/v1/chat (SSE-buffered).

        DDG ALWAYS streams — even for this non-streaming entry point,
        the SSE stream is consumed and buffered. ``tools`` /
        ``temperature`` / ``max_tokens`` / ``think`` are accepted for
        interface parity and SILENTLY DROPPED (upstream sampling
        defaults apply; a debug-mode notice fires for temperature).

        Proof-expiry recovery: a 401 or an SSE ``ERR_CHALLENGE`` error
        triggers ONE automatic re-solve (fresh challenge + proof) and
        a single retry of the /chat call. An HTTP 418 teapot
        (``ERR_CHALLENGE`` at the HTTP layer — proof rejected or the
        anti-bot challenge ladder escalated) gets up to
        ``_CHALLENGE_RETRIES`` re-solves, preferring the challenge
        carried on the 418's own ``x-vqd-hash-1`` response header and
        escalating the proof's reported duration to a plausible floor
        (strict routes flag sub-100ms solves).

        Returns the house response shape (content / tool_calls /
        usage / finish_reason). ``usage`` is an ~4-chars/token
        ESTIMATE (the DDG protocol reports no usage) flagged with
        ``"estimated": True``.
        """
        wire_model = _resolve_model(model)
        convo = self._strip_system_messages(messages)
        if not convo:
            raise ValueError(
                "DuckDuckGo generate(): no sendable messages "
                "(system-only conversations are dropped — DDG never "
                "receives the system prompt)"
            )
        if temperature is not None and os.environ.get("AGENTKTHX_DEBUG"):
            print(
                "  [duckduckgo] 'temperature' ignored — DDG uses upstream "
                "defaults (no sampling params in the protocol)"
            )

        self._scrape_fe_meta()
        body = self._build_chat_body(wire_model, convo)

        attempt = 0
        duration_floor = 0
        while True:
            attempt += 1
            self._pace_chat()
            proof = self._acquire_proof(min_duration_ms=duration_floor)
            req = urllib.request.Request(
                f"{self.base_url}/duckchat/v1/chat",
                data=body,
                headers=self._build_chat_headers(proof),
                method="POST",
            )
            try:
                content, _sources, next_chal = self._consume_sse(req)
            except urllib.error.HTTPError as e:
                if e.code == 418:
                    # Teapot = ERR_CHALLENGE at the HTTP layer: the proof
                    # was rejected (expired / shape-flagged) or the route
                    # demands a harder challenge round. Climb the ladder:
                    # prefer the challenge carried on THIS response's
                    # x-vqd-hash-1 header, else re-bootstrap /status.
                    detail = e.read()[:2000].decode("utf-8", "replace")
                    ladder = (e.headers.get("x-vqd-hash-1") or None) if e.headers else None
                    if attempt <= self._CHALLENGE_RETRIES:
                        self._next_challenge_b64 = ladder
                        duration_floor = _PROOF_DURATION_FLOOR_MS
                        self._log_418(attempt, detail, ladder)
                        time.sleep(_CHALLENGE_RETRY_SLEEP_S)
                        continue
                    raise RuntimeError(
                        f"DuckDuckGo ERR_CHALLENGE (HTTP 418) persisted after "
                        f"{self._CHALLENGE_RETRIES} proof re-solves — the "
                        "anti-bot layer is rejecting the proof shape for "
                        "this model/route. Try another model or wait a "
                        "minute (proofs expire); body: "
                        f"{detail[:200]}"
                    ) from e
                if e.code == 401 and attempt == 1:
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print("  [duckduckgo] 401 proof rejected — re-solving")
                    self._next_challenge_b64 = None
                    continue
                if e.code == 404:
                    detail = e.read()[:200].decode("utf-8", "replace")
                    raise RuntimeError(
                        f"DuckDuckGo model unavailable ({wire_model}): "
                        f"{detail} — the catalog rotated; run "
                        "`agentkthx models --backend duckduckgo` after a "
                        "seed update (see "
                        "docs/api/DUCKDUCKGO_API_TECHNICAL_REFERENCE.md)"
                    ) from e
                if e.code == 403:
                    raise RuntimeError(
                        "DuckDuckGo 403 Forbidden — the anti-bot layer "
                        "rejected the request. Check DUCKDUCKGO_USER_AGENT "
                        "(must look like a real browser) — the Referer and "
                        "Accept headers are fixed by the backend."
                    ) from e
                if e.code == 429:
                    retry_after = (e.headers.get("Retry-After") or "").strip() if e.headers else ""
                    suffix = f" Server Retry-After: {retry_after}s." if retry_after else ""
                    raise RuntimeError(
                        "DuckDuckGo rate limit (429) — per-IP throttling, "
                        "undocumented limits. Back off 5-10s between "
                        "requests; if this persists, reduce request "
                        f"frequency or route through a different network.{suffix}"
                    ) from e
                if e.code >= 500:
                    raise RuntimeError(
                        f"DuckDuckGo upstream error ({e.code}) — an upstream "
                        "provider may be down. Try a different DDG model."
                    ) from e
                raise
            except _ConversationLimitError:
                raise RuntimeError(
                    "DuckDuckGo conversation limit reached (~20 turns). "
                    "Start a fresh conversation: clear agent memory "
                    "(/clear in chat) or restart the session."
                )
            except _ChallengeError:
                if attempt == 1:
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print("  [duckduckgo] ERR_CHALLENGE — re-solving proof")
                    self._next_challenge_b64 = None
                    continue
                raise RuntimeError(
                    "DuckDuckGo ERR_CHALLENGE persisted after proof "
                    "re-solve — the challenge format rotated; see "
                    "docs/api/DUCKDUCKGO_API_TECHNICAL_REFERENCE.md"
                )

            self._next_challenge_b64 = next_chal
            prompt_text = "".join(m.get("content", "") for m in convo)
            return {
                "content": content,
                "tool_calls": [],  # ReAct only — canUseTools ships false
                "usage": {
                    "prompt_tokens": _estimate_tokens(prompt_text),
                    "completion_tokens": _estimate_tokens(content),
                    "total_tokens": _estimate_tokens(prompt_text) + _estimate_tokens(content),
                    "estimated": True,
                },
                "finish_reason": "stop",
                "model": wire_model,
            }

    def _open_chat_stream(
        self, wire_model: str, convo: list[dict], min_duration_ms: int = 0
    ) -> urllib.request.Request:
        """Build the POST /chat request (proof acquired, meta scraped)."""
        self._scrape_fe_meta()
        body = self._build_chat_body(wire_model, convo)
        proof = self._acquire_proof(min_duration_ms=min_duration_ms)
        return urllib.request.Request(
            f"{self.base_url}/duckchat/v1/chat",
            data=body,
            headers=self._build_chat_headers(proof),
            method="POST",
        )

    def _consume_sse(self, req: urllib.request.Request) -> tuple[str, list[dict], str | None]:
        """POST /chat and consume the SSE stream to completion.

        Returns ``(text, sources, next_challenge)``. Raises HTTPError
        for non-200 statuses, ``_ChallengeError`` for ERR_CHALLENGE
        (retryable via re-solve), ``_ConversationLimitError`` for
        ERR_CONVERSATION_LIMIT / [LIMIT_CONVERSATION], and RuntimeError
        for other SSE error frames.

        Event grammar (role-based, current; action-based kept as
        legacy fallback — see ``_classify_sse_payload``). Streams may
        also simply END without [DONE] — an exhausted stream with
        content is a success.
        """
        parts: list[str] = []
        sources: list[dict] = []
        with urllib.request.urlopen(req, timeout=180) as resp:
            next_challenge = resp.headers.get("x-vqd-hash-1")
            buffer = b""
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw_line, buffer = buffer.split(b"\n", 1)
                    line = raw_line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    ev = _classify_sse_payload(payload)
                    if ev is None:
                        continue
                    kind = ev["kind"]
                    if kind == "text":
                        parts.append(ev["text"])
                    elif kind == "source":
                        sources.append({"url": ev["url"], "title": ev["title"]})
                    elif kind == "done":
                        return "".join(parts), sources, next_challenge
                    elif kind == "limit":
                        raise _ConversationLimitError()
                    elif kind == "error":
                        if ev["type"] == "ERR_CHALLENGE":
                            raise _ChallengeError()
                        if ev["type"] == "ERR_CONVERSATION_LIMIT":
                            raise _ConversationLimitError()
                        raise RuntimeError(
                            f"DuckDuckGo SSE error: {ev['type'] or 'unknown'} "
                            f"(status={ev['status']})"
                        )
                    # ping / title / tool / ui — no text to buffer
        return "".join(parts), sources, next_challenge

    def generate_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ):
        """TRUE incremental generation (FEAT-10 — closed).

        Yields text deltas as they arrive off the SSE stream. The
        proof lifecycle is identical to ``generate()`` (acquire →
        POST → next-challenge rotation); challenge errors mid-stream
        re-solve ONCE and replay the request (already-yielded text
        is NOT re-yielded — the retry restarts the turn), and HTTP
        418 teapots climb the challenge ladder (up to
        ``_CHALLENGE_RETRIES`` re-solves, the 418's response-header
        challenge preferred over a /status re-bootstrap). Error
        taxonomy mirrors ``generate()`` exactly (404 catalog rotation,
        403 anti-bot, 418 teapot, 429 rate limit, 5xx upstream,
        conversation limit) — the streaming path is FEAT-10's whole
        point, so it must surface the same remediation messages the
        buffered path does.
        """
        wire_model = _resolve_model(model)
        convo = self._strip_system_messages(messages)
        if not convo:
            raise ValueError(
                "DuckDuckGo generate_stream(): no sendable messages "
                "(system-only conversations are dropped — DDG never "
                "receives the system prompt)"
            )

        attempt = 0
        duration_floor = 0
        while True:
            attempt += 1
            self._pace_chat()
            req = self._open_chat_stream(wire_model, convo, min_duration_ms=duration_floor)
            retryable = False
            reset_challenge = True
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    self._next_challenge_b64 = resp.headers.get("x-vqd-hash-1")
                    buffer = b""
                    while True:
                        chunk = resp.read(4096)
                        if not chunk:
                            return
                        buffer += chunk
                        while b"\n" in buffer:
                            raw_line, buffer = buffer.split(b"\n", 1)
                            line = raw_line.strip()
                            if not line.startswith(b"data:"):
                                continue
                            payload = line[5:].strip()
                            if not payload:
                                continue
                            ev = _classify_sse_payload(payload)
                            if ev is None:
                                continue
                            kind = ev["kind"]
                            if kind == "text":
                                yield ev["text"]
                            elif kind == "done":
                                return
                            elif kind == "limit":
                                raise _ConversationLimitError()
                            elif kind == "error":
                                if ev["type"] == "ERR_CHALLENGE":
                                    raise _ChallengeError()
                                if ev["type"] == "ERR_CONVERSATION_LIMIT":
                                    raise _ConversationLimitError()
                                raise RuntimeError(
                                    f"DuckDuckGo SSE error: "
                                    f"{ev['type'] or 'unknown'} "
                                    f"(status={ev['status']})"
                                )
            except urllib.error.HTTPError as e:
                if e.code == 418:
                    # Teapot ladder — mirror of the buffered path: prefer
                    # the challenge on THIS response's x-vqd-hash-1 header,
                    # else re-bootstrap from /status, duration floor on.
                    detail = e.read()[:2000].decode("utf-8", "replace")
                    ladder = (e.headers.get("x-vqd-hash-1") or None) if e.headers else None
                    if attempt <= self._CHALLENGE_RETRIES:
                        self._next_challenge_b64 = ladder
                        reset_challenge = False
                        duration_floor = _PROOF_DURATION_FLOOR_MS
                        self._log_418(attempt, detail, ladder)
                        time.sleep(_CHALLENGE_RETRY_SLEEP_S)
                        retryable = True
                    else:
                        raise RuntimeError(
                            f"DuckDuckGo ERR_CHALLENGE (HTTP 418) persisted "
                            f"after {self._CHALLENGE_RETRIES} proof "
                            "re-solves — the anti-bot layer is rejecting "
                            "the proof shape for this model/route. Try "
                            "another model or wait a minute (proofs "
                            f"expire); body: {detail[:200]}"
                        ) from e
                elif e.code == 401 and attempt == 1:
                    retryable = True
                elif e.code == 404:
                    detail = e.read()[:200].decode("utf-8", "replace")
                    raise RuntimeError(
                        f"DuckDuckGo model unavailable ({wire_model}): "
                        f"{detail} — the catalog rotated (see "
                        "docs/api/DUCKDUCKGO_API_TECHNICAL_REFERENCE.md)"
                    ) from e
                elif e.code == 403:
                    raise RuntimeError(
                        "DuckDuckGo 403 Forbidden — the anti-bot layer "
                        "rejected the request. Check DUCKDUCKGO_USER_AGENT "
                        "(must look like a real browser) — the Referer and "
                        "Accept headers are fixed by the backend."
                    ) from e
                elif e.code == 429:
                    retry_after = (e.headers.get("Retry-After") or "").strip() if e.headers else ""
                    suffix = f" Server Retry-After: {retry_after}s." if retry_after else ""
                    raise RuntimeError(
                        "DuckDuckGo rate limit (429) — per-IP throttling, "
                        "undocumented limits. Back off 5-10s between "
                        "requests; if this persists, reduce request "
                        f"frequency or route through a different network.{suffix}"
                    ) from e
                elif e.code >= 500:
                    raise RuntimeError(
                        f"DuckDuckGo upstream error ({e.code}) — an upstream "
                        "provider may be down. Try a different DDG model."
                    ) from e
                else:
                    raise
            except _ChallengeError:
                if attempt == 1:
                    retryable = True
                else:
                    raise RuntimeError(
                        "DuckDuckGo ERR_CHALLENGE persisted after proof "
                        "re-solve — the challenge format rotated; see "
                        "docs/api/DUCKDUCKGO_API_TECHNICAL_REFERENCE.md"
                    )
            except _ConversationLimitError:
                raise RuntimeError(
                    "DuckDuckGo conversation limit reached (~20 turns). "
                    "Start a fresh conversation: clear agent memory "
                    "(/clear in chat) or restart the session."
                )
            if retryable:
                if reset_challenge:
                    self._next_challenge_b64 = None
                continue

    # ─────────────────────────────────────────────────────────────────────
    # Catalog — seed-backed offline list + live capabilities helper
    # ─────────────────────────────────────────────────────────────────────

    def list_models(self) -> list[dict]:
        """Serve the seed catalog (cached 1h, offline-safe).

        The seed IS the 8-model wire catalog (client-verified IDs).
        The LIVE catalog is available via ``fetch_capabilities()``
        (GET /duckchat/v1/capabilities) — kept out of this path so
        `agentkthx models --backend duckduckgo` renders fully
        offline (the house contract: no network in model listing).
        Routed through the persistent model cache so
        ``--cache-status`` stays uniform.
        """
        now = time.time()
        if (
            self._model_cache is not None
            and (now - self._model_cache_ts) < self._MODEL_CACHE_TTL_SECONDS
        ):
            return list(self._model_cache)

        cached = model_cache.get_cached_models(self.MODEL_CACHE_KEY)
        if cached is not None:
            self._model_cache = cached
            self._model_cache_ts = now
            return list(cached)

        models = self._catalog_fallback_list()
        self._model_cache = model_cache.store_models(self.MODEL_CACHE_KEY, models)
        self._model_cache_ts = now
        return list(self._model_cache)

    def _catalog_fallback_list(self) -> list[dict]:
        """Shape the seed catalog into ``list_models()`` entries.

        Every DDG model is free (pricing 0.0/0.0) and chat-capable.
        The ``think`` verdict is NO across the board — see
        ``test_thinking_support``.
        """
        models: list[dict] = []
        for name in sorted(DUCKDUCKGO_MODELS.keys()):
            meta = DUCKDUCKGO_MODELS[name]
            models.append(
                {
                    "name": name,
                    "size": 0,
                    "details": {
                        "family": meta.get("family", "duckduckgo"),
                        "backend": "duckduckgo",
                        "context_length": meta.get("context_length", 128000),
                        "free_tier": True,
                        "is_chat_model": True,
                        "pricing": {"input": 0.0, "output": 0.0},
                    },
                }
            )
        return models

    def fetch_capabilities(self) -> dict | None:
        """Live model catalog via GET /duckchat/v1/capabilities.

        The browser bootstrap sequence hits this endpoint before any
        challenge is solved (unauthenticated model discovery). When a
        deployment gates it behind the proof, this retries once with
        a solved X-Vqd-Hash-1. Returns the parsed JSON or None (any
        failure — this is a discovery helper, never a hard dep).
        """
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{self.base_url}/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        def _get(extra: dict | None = None) -> bytes:
            merged = dict(headers)
            if extra:
                merged.update(extra)
            req = urllib.request.Request(
                f"{self.base_url}/duckchat/v1/capabilities",
                headers=merged,
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read()

        try:
            body = _get()
        except urllib.error.HTTPError as e:
            try:
                e.read()
            except Exception:  # noqa: BLE001 — drain best-effort
                pass
            if e.code in (401, 403):
                try:
                    body = _get({"X-Vqd-Hash-1": self._acquire_proof()})
                except Exception:  # noqa: BLE001 — discovery helper
                    return None
            else:
                return None
        except (urllib.error.URLError, OSError, ValueError):
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def get_model_info(self, model: str) -> dict | None:
        """Catalog lookup with alias resolution."""
        resolved = _resolve_model(model)
        for m in self.list_models():
            if m.get("name") == resolved:
                return m
        return None

    def get_model_max_context(self, model: str, family: str | None = None) -> int:
        """Context length from the seed catalog (alias-aware).

        Unknown models get the conservative 128K default — the DDG
        lineup clusters at 128K-200K and an over-estimate risks
        context-length 400s DDG can't recover from.
        """
        info = self.get_model_info(model)
        if info and info.get("details", {}).get("context_length"):
            return int(info["details"]["context_length"])
        return 128000

    def get_model_runtime_context(self, model: str) -> int:
        """Runtime context — equals max context (CloudBackend contract).

        Cloud-style backends have no Ollama-style Modelfile ``num_ctx``
        to shrink the window at runtime; DDG serves the model's full
        context. ``cmd_models`` calls this unconditionally on every
        backend, so it must exist even though its result feeds no
        column today.
        """
        return self.get_model_max_context(model)

    # ─────────────────────────────────────────────────────────────────────
    # Capability verdicts — both are protocol facts, not probes
    # ─────────────────────────────────────────────────────────────────────

    def test_tool_support(
        self,
        model: str,
        family: str | None = None,
        force_test: bool = False,
    ) -> ToolSupportLevel:
        """REACT for every DDG model — we ship canUseTools=false.

        The wire now HAS a native tools surface (canUseTools +
        metadata.toolChoice: WebSearch / GenerateImage / NewsSearch /
        VideosSearch / LocalSearch / WeatherForecast), but it is
        undocumented and rotates independently of the chat endpoint.
        Until it stabilizes this backend sends canUseTools=false and
        the agent loop uses the ReAct prompting path (the textual
        Action/Action Input format). PROTOCOL DECISION, not a probe.
        """
        return ToolSupportLevel.REACT

    def test_thinking_support(
        self,
        model: str,
        family: str | None = None,
        force_test: bool = False,
    ) -> ThinkingSupport:
        """NO for every DDG model — the privacy layer hides reasoning.

        ThinkingSupport answers "does the model emit reasoning_content
        (chain of thought) into the response?". Even the reasoning
        models (gpt-oss-120b, served with reasoningEffort "low") never
        surface CoT through the DDG SSE shape — only ``message`` text
        arrives. NO is the honest verdict for the whole catalog;
        ``--think`` has nothing to display on this backend.
        """
        return ThinkingSupport.NO

    # ─────────────────────────────────────────────────────────────────────
    # Liveness
    # ─────────────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """Always True — keyless backend, static catalog, no local server.

        House contract (see CloudBackend): ``is_running`` is a CHEAP
        local check that gates ``cmd_models`` — cloud backends must
        not touch the network here (ZAI/OpenRouter/etc. check their
        API key; DDG has none to check). This must NOT check node
        availability either (a missing node surfaces at generate()
        time with its remediation message). Real liveness is proven
        by generate()'s own /status → solve → /chat handshake, with
        the 401/ERR_CHALLENGE re-solve and error taxonomy as the
        failure surface.
        """
        return True
