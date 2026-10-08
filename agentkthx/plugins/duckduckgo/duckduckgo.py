"""
⚛️ AgentKthx — DuckDuckGo AI Chat Backend
Backend implementation for the DuckDuckGo AI Chat (duck.ai) API.

DuckDuckGo AI Chat is the keyless, anonymous, zero-cost LLM surface:
no API key, no signup, no quota. Frontier upstream models (OpenAI
GPT-4o mini / o3-mini, Anthropic Claude Haiku, Meta Llama 3.3 70B,
Mistral Small 3 24B) proxied through DuckDuckGo's privacy layer,
which strips your IP before forwarding upstream.

This backend is DELIBERATELY unlike every other AgentKthx cloud
backend. The protocol is NOT OpenAI Chat-Completions — it is
DuckDuckGo's own /duckchat/v1/* wire format:

  - GET  /duckchat/v1/status → bootstrap the x-vqd-4 session token
    (returned in a response HEADER, empty body)
  - POST /duckchat/v1/chat   → {model, messages} body, ALWAYS
    streamed back as DuckDuckGo's own SSE shape
    (``data: {"action": "chunk", "message": ...}``)

Because of that, this backend subclasses BaseBackend directly — it
does NOT inherit CloudBackend (no _make_api_request retry skeleton,
no _get_auth_headers, no OpenAI body builder). The R07.28 shared
transport is OpenAI-compat shaped and would fight this protocol at
every seam. Nothing in backends/cloud_base.py or openai_compat.py
was modified for this scaffold.

Protocol constraints honored here:
  - No ``system`` role — DDG strips it. ``_collapse_system_into_user``
    prepends system content to the first user message.
  - No ``tools`` / ``tool_choice`` / ``tool_calls`` — DDG strips them.
    ``test_tool_support()`` returns REACT for every model; the agent
    loop uses the ReAct prompting path (``force_react`` semantics).
  - No sampling params — temperature / max_tokens / top_p / stop /
    seed are accepted at the interface and silently dropped (the
    request body is exactly ``{model, messages}``).
  - Always-streaming — responses arrive as SSE even when the caller
    asked for ``stream=False``; ``generate()`` buffers the stream
    internally. True incremental ``generate_stream()`` is a
    registered follow-up (FEAT-10).
  - x-vqd-4 token rotation — every /chat response carries a fresh
    token in its headers; the old token is invalidated. State is
    capped at 2 entries (current + previous) per the mumu-lhl
    client contract.
  - Conversation limit — ~20 turns per conversation; surfaces as a
    429 SSE error chunk with type ``ERR_CONVERSATION_LIMIT`` or the
    bare ``[LIMIT_CONVERSATION]`` terminal marker. Remediation is
    clearing the agent's memory (start a fresh conversation).
  - Anti-bot — the User-Agent MUST look like a real browser and
    Referer MUST be https://duckduckgo.com/ or DDG returns 403.

Model catalog (verified against the actively-maintained
mumu-lhl/duckduckgo-ai-chat v3.3.0 client — the same source the API
reference cites — cross-checked with the Oct 2026 duck.ai help page
lineup of GPT-4o mini / Claude Haiku / Llama 3.3 70B / Mistral
Small 3 24B; the "claude-3-haiku" alias still resolves to the
claude-3-haiku-20240307 wire ID, which fronts whatever Haiku
version DDG currently serves behind it):

  - gpt-4o-mini
  - claude-3-haiku-20240307
  - meta-llama/Llama-3.3-70B-Instruct-Turbo
  - mistralai/Mistral-Small-24B-Instruct-2501
  - o3-mini

Legacy IDs from the mrgick/duck_chat era (Llama 3.1 70B Turbo,
Mixtral-8x7B) are NOT seeded — the upstream lineup rotated past
them. The short aliases "claude-3-haiku", "llama", and "mixtral"
resolve to their current successors (same mapping mumu-lhl ships).

Usage:
    AGENTKTHX_BACKEND=duckduckgo agentkthx chat -m gpt-4o-mini
    agentkthx models --backend duckduckgo

See docs/api/DUCKDUCKGO_API_TECHNICAL_REFERENCE.md for full details.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from agentkthx import model_cache
from agentkthx.backends.base import BaseBackend
from agentkthx.config import DUCKDUCKGO_BASE_URL, DUCKDUCKGO_DEFAULT_MODEL, DUCKDUCKGO_USER_AGENT
from agentkthx.core.types import BackendType, ThinkingSupport, ToolSupportLevel
from agentkthx.model_cache import load_seed_catalog

#: Default browser User-Agent — DDG returns 403 for non-browser UAs.
#: Overridable via the DUCKDUCKGO_USER_AGENT env var.
_DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

#: Static catalog from agentkthx/data/model_seed.json — the 5
#: client-verified DDG chat models with context metadata. DDG has no
#: /models endpoint, so this IS the catalog (no live fetch to merge).
DUCKDUCKGO_MODELS: dict[str, dict] = load_seed_catalog("duckduckgo")

#: Default model when none specified. gpt-4o-mini is DuckDuckGo's own
#: default (the natural zero-config pick); override via
#: DUCKDUCKGO_DEFAULT_MODEL.
DUCKDUCKGO_DEFAULT_MODEL_STR = DUCKDUCKGO_DEFAULT_MODEL or "gpt-4o-mini"

#: Short-alias → canonical wire-ID map. Mirrors the mumu-lhl v3.3.0
#: client exactly: "claude-3-haiku" → dated Anthropic ID, "llama" →
#: the 3.3 70B Turbo path, "mixtral" → Mistral Small 3 24B (the
#: Mixtral slot in the lineup was replaced by Mistral Small).
_MODEL_ALIASES: dict[str, str] = {
    "claude-3-haiku": "claude-3-haiku-20240307",
    "llama": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "mixtral": "mistralai/Mistral-Small-24B-Instruct-2501",
}

#: SSE terminal markers beyond ``[DONE]`` observed in the wild —
#: mrgick/duck_chat strips a trailing ``[LIMIT_CONVERSATION]`` from
#: the stream body; treat it as the conversation-limit signal.
_LIMIT_MARKER = "[LIMIT_CONVERSATION]"


def _resolve_model(model: str) -> str:
    """Resolve a short alias to its canonical DDG wire ID.

    Case-insensitive on the alias table only — canonical IDs pass
    through untouched (DDG IDs are case-sensitive upstream).
    """
    if model in _MODEL_ALIASES:
        return _MODEL_ALIASES[model]
    lower = model.lower()
    if lower in _MODEL_ALIASES:
        return _MODEL_ALIASES[lower]
    return model


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — DDG reports no usage.

    Same heuristic the Agent's TPS fallback uses. The usage block in
    generate()'s response is therefore an ESTIMATE, flagged via the
    ``estimated`` key so callers can tell.
    """
    return max(1, len(text) // 4) if text else 0


class _ChallengeError(Exception):
    """SSE ``ERR_CHALLENGE`` — x-vqd-4 missing/malformed; re-bootstrap."""


class _ConversationLimitError(Exception):
    """Conversation limit (~20 turns) — clear memory, start fresh."""


class DuckDuckGoBackend(BaseBackend):
    """DuckDuckGo AI Chat backend — keyless, anonymous, non-OpenAI-compat.

    The ONLY AgentKthx cloud backend that subclasses BaseBackend
    directly instead of CloudBackend: the /duckchat/v1 protocol
    (x-vqd-4 header-token handshake, own SSE shape, no /models, no
    sampling params, no tools) shares nothing with the OpenAI-compat
    transport the shared cloud skeleton is built around.

    What this class owns:
      - x-vqd-4 token lifecycle (bootstrap via /status, rotation on
        every /chat response header, 2-entry cap, single re-bootstrap
        retry on 401 / ERR_CHALLENGE)
      - SSE stream parsing (start/chunk/success/error actions,
        [DONE] and [LIMIT_CONVERSATION] terminals)
      - system-role collapsing (DDG strips system messages)
      - alias resolution + static catalog serving

    What it deliberately does NOT do:
      - native tool calling (DDG strips tools — always REACT)
      - sampling param forwarding (upstream defaults apply)
      - streaming generation (FEAT-10 follow-up — generate() buffers)
      - JEV decision mode (needs JSON output control DDG lacks)
      - any auth (there is no key — anonymous by design)
    """

    is_cloud = True

    # Persistent JSON model-catalog cache key (~/.cache/agentkthx/
    # model_catalog.json under "duckduckgo"). Even though the catalog
    # is static, routing list_models() through the same cache keeps
    # `agentkthx models --cache-status` uniform across backends.
    MODEL_CACHE_KEY = "duckduckgo"

    #: L1 in-process catalog cache TTL — the DDG lineup only changes
    #: when DDG rotates upstreams, so 1 hour matches the house default.
    _MODEL_CACHE_TTL_SECONDS = 3600

    _model_cache: list[dict] | None = None

    def __init__(
        self,
        config=None,
        base_url: str | None = None,
        api_mode: object | None = None,
        user_agent: str | None = None,
        **kwargs,
    ):
        super().__init__(config=config, base_url=base_url, api_mode=api_mode)

        #: Browser UA (DDG 403s non-browser UAs). Precedence:
        #: explicit kwarg > DUCKDUCKGO_USER_AGENT env > Chrome 120.
        self._user_agent = user_agent or DUCKDUCKGO_USER_AGENT or _DEFAULT_UA

        #: x-vqd-4 session token pair (capped at 2 per the mumu-lhl
        #: oldVqd/newVqd contract — AgentKthx's loop is forward-only,
        #: so the previous token is kept purely for diagnostics).
        self._vqd_current: str | None = None
        self._vqd_previous: str | None = None

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
        """API root — https://duckduckgo.com (protocol lives under /duckchat/v1)."""
        return self._base_url or DUCKDUCKGO_BASE_URL

    # ─────────────────────────────────────────────────────────────────────
    # Headers (anti-bot contract: browser UA + Referer + SSE Accept)
    # ─────────────────────────────────────────────────────────────────────

    def _build_status_headers(self) -> dict:
        """Headers for the /status token bootstrap.

        ``x-vqd-accept: 1`` tells DDG to issue a vqd token. The
        browser-like UA / Referer / Accept trio is REQUIRED — missing
        any of them draws a 403 from the anti-bot layer.
        """
        return {
            "Host": "duckduckgo.com",
            "Accept": "text/event-stream",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://duckduckgo.com/",
            "User-Agent": self._user_agent,
            "x-vqd-accept": "1",
            "DNT": "1",
            "Sec-GPC": "1",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    def _build_chat_headers(self, vqd_token: str) -> dict:
        """Headers for a /chat call — carries the current x-vqd-4 token."""
        return {
            "Host": "duckduckgo.com",
            "Accept": "text/event-stream",
            "Accept-Language": "en-US,en;q=0.5",
            "Content-Type": "application/json",
            "Referer": "https://duckduckgo.com/",
            "User-Agent": self._user_agent,
            "x-vqd-4": vqd_token,
            "DNT": "1",
            "Sec-GPC": "1",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    # ─────────────────────────────────────────────────────────────────────
    # x-vqd-4 token lifecycle
    # ─────────────────────────────────────────────────────────────────────

    def _bootstrap_vqd(self) -> str:
        """GET /duckchat/v1/status → fresh x-vqd-4 from the response header.

        The body is EMPTY — the token rides the ``x-vqd-4`` response
        header. A 429 here is the /status rate limit (per-IP
        throttling; back off, don't hammer).
        """
        req = urllib.request.Request(
            f"{self.base_url}/duckchat/v1/status",
            headers=self._build_status_headers(),
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            token = resp.headers.get("x-vqd-4")
            if not token:
                raise RuntimeError(
                    "DuckDuckGo /status returned no x-vqd-4 token "
                    f"(status {resp.status}) — the protocol may have changed; "
                    "see docs/api/DUCKDUCKGO_API_TECHNICAL_REFERENCE.md"
                )
            self._rotate_vqd(token)
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [duckduckgo] bootstrapped x-vqd-4 token: {token[:16]}...")
            return token

    def _rotate_vqd(self, fresh: str | None) -> None:
        """Store a freshly issued token (current → previous, 2-cap)."""
        if not fresh:
            # mrgick's client appends "" on missing headers; an empty
            # token would 401 the next call — keep the current one.
            return
        if fresh != self._vqd_current:
            self._vqd_previous = self._vqd_current
            self._vqd_current = fresh

    def _get_vqd(self) -> str:
        """Current token, bootstrapping on first use."""
        if not self._vqd_current:
            return self._bootstrap_vqd()
        return self._vqd_current

    # ─────────────────────────────────────────────────────────────────────
    # Message shaping
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _coerce_content(content) -> str:
        """Flatten message content to a plain string.

        AgentKthx messages are string-content, but defensive handling
        of OpenAI-style ``[{type: text, text: ...}]`` part lists keeps
        the backend usable from hand-rolled callers.
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("text"):
                    parts.append(str(part["text"]))
                elif isinstance(part, str):
                    parts.append(part)
            return "\n".join(p for p in parts if p)
        return str(content) if content is not None else ""

    def _collapse_system_into_user(self, messages: list[dict]) -> list[dict]:
        """DDG strips ``system`` role messages — fold them into user[0].

        All system content is concatenated and prepended to the first
        user message (the documented DDG workaround). Non-user/assistant
        roles (tool/function — shouldn't occur under forced ReAct, but
        defensive) are folded in as user context so nothing is silently
        dropped from the transcript.
        """
        if not messages:
            return []

        system_parts: list[str] = []
        convo: list[dict] = []
        for msg in messages:
            role = (msg.get("role") or "").lower()
            content = self._coerce_content(msg.get("content"))
            if role == "system":
                if content:
                    system_parts.append(content)
            elif role in ("user", "assistant"):
                convo.append({"role": role, "content": content})
            else:
                # Unknown/tool-ish role — keep the text as user context
                if content:
                    convo.append({"role": "user", "content": content})

        if system_parts:
            if convo:
                first_user = next((m for m in convo if m["role"] == "user"), None)
                prefix = "\n\n".join(system_parts)
                if first_user:
                    first_user["content"] = f"{prefix}\n\n{first_user['content']}"
                else:
                    convo.insert(0, {"role": "user", "content": prefix})
            else:
                convo.append({"role": "user", "content": "\n\n".join(system_parts)})
        return convo

    # ─────────────────────────────────────────────────────────────────────
    # generate — buffered-SSE entry point (the ONLY generation path)
    # ─────────────────────────────────────────────────────────────────────

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
        the SSE stream is consumed and buffered server-side by this
        method. ``tools`` / ``temperature`` / ``max_tokens`` / ``think``
        are accepted for interface parity and SILENTLY DROPPED: the DDG
        request body is exactly ``{model, messages}`` and upstream
        sampling defaults apply. (A debug-mode notice is printed when
        ``temperature`` is explicitly supplied.)

        Token-expiry recovery: a 401 or an SSE ``ERR_CHALLENGE`` error
        triggers ONE automatic re-bootstrap (fresh /status token) and
        a single retry of the /chat call — the documented
        inactivity-expiry remediation.

        Returns the house response shape (content / tool_calls /
        usage / finish_reason). ``usage`` is an ~4-chars/token
        ESTIMATE (the DDG protocol reports no usage) flagged with
        ``"estimated": True``.
        """
        wire_model = _resolve_model(model)
        convo = self._collapse_system_into_user(messages)
        if not convo:
            raise ValueError("DuckDuckGo generate(): no messages after system collapse")

        if temperature is not None and os.environ.get("AGENTKTHX_DEBUG"):
            print(
                "  [duckduckgo] 'temperature' ignored — DDG uses upstream "
                "defaults (no sampling params in the protocol)"
            )

        body = json.dumps({"model": wire_model, "messages": convo}).encode("utf-8")

        # Single auto-re-bootstrap on token-expiry signals (401 /
        # ERR_CHALLENGE) — one retry, then surface.
        attempt = 0
        while True:
            attempt += 1
            token = self._get_vqd()
            req = urllib.request.Request(
                f"{self.base_url}/duckchat/v1/chat",
                data=body,
                headers=self._build_chat_headers(token),
                method="POST",
            )
            try:
                content, fresh_token = self._consume_sse(req)
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 1:
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print("  [duckduckgo] 401 token expired — re-bootstrapping")
                    self._vqd_current = None
                    continue
                if e.code == 403:
                    raise RuntimeError(
                        "DuckDuckGo 403 Forbidden — the anti-bot layer "
                        "rejected the request. Check DUCKDUCKGO_USER_AGENT "
                        "(must look like a real browser) — the Referer and "
                        "Accept headers are fixed by the backend."
                    ) from e
                if e.code == 429:
                    raise RuntimeError(
                        "DuckDuckGo rate limit (429) — per-IP throttling, "
                        "undocumented limits. Back off 5-10s between "
                        "requests; if this persists, reduce request "
                        "frequency or route through a different network."
                    ) from e
                if e.code >= 500:
                    raise RuntimeError(
                        f"DuckDuckGo upstream error ({e.code}) — an upstream "
                        "provider (OpenAI/Anthropic/Meta/Mistral) may be "
                        "down. Try a different DDG model."
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
                        print("  [duckduckgo] ERR_CHALLENGE — re-bootstrapping token")
                    self._vqd_current = None
                    continue
                raise RuntimeError(
                    "DuckDuckGo ERR_CHALLENGE persisted after token "
                    "re-bootstrap — the protocol may have changed; see "
                    "docs/api/DUCKDUCKGO_API_TECHNICAL_REFERENCE.md"
                )

            self._rotate_vqd(fresh_token)
            prompt_text = "".join(m["content"] for m in convo)
            return {
                "content": content,
                "tool_calls": [],  # DDG strips tool_calls — ReAct only
                "usage": {
                    "prompt_tokens": _estimate_tokens(prompt_text),
                    "completion_tokens": _estimate_tokens(content),
                    "total_tokens": _estimate_tokens(prompt_text) + _estimate_tokens(content),
                    "estimated": True,
                },
                "finish_reason": "stop",
                "model": wire_model,
            }

    def _consume_sse(self, req: urllib.request.Request) -> tuple[str, str | None]:
        """POST /chat and consume the DDG SSE stream to completion.

        Returns ``(text, fresh_token)``. Raises HTTPError for non-200
        statuses, ``_ChallengeError`` for the ERR_CHALLENGE SSE error
        (retryable via re-bootstrap), ``_ConversationLimitError`` for
        ERR_CONVERSATION_LIMIT / the [LIMIT_CONVERSATION] marker, and
        RuntimeError for other SSE error chunks.

        Stream grammar (from the reverse-engineered clients):
          data: {"action": "start",    "model": ...}          (no message)
          data: {"action": "chunk",   "message": "<delta>"}
          data: {"action": "success", "model": ...}          (no message)
          data: {"action": "error",   "type": ..., "status": ...}
          data: [DONE]                                       (terminal)
          data: [LIMIT_CONVERSATION]                         (terminal —
                                                              conv limit)
        Streams may also simply END without a terminal marker — the
        mumu-lhl client treats a message-less chunk as the stop
        signal, so an exhausted stream with content is a success.
        """
        parts: list[str] = []
        with urllib.request.urlopen(req, timeout=180) as resp:
            fresh_token = resp.headers.get("x-vqd-4")
            buffer = b""
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw_line, buffer = buffer.split(b"\n", 1)
                    line = raw_line.strip()
                    if not line.startswith(b"data: "):
                        continue
                    payload = line[6:]
                    if payload == b"[DONE]":
                        return "".join(parts), fresh_token
                    if payload == b"[LIMIT_CONVERSATION]":
                        raise _ConversationLimitError()
                    try:
                        data = json.loads(payload.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue  # keep-alive/comment noise
                    if not isinstance(data, dict):
                        continue
                    action = data.get("action")
                    if action == "chunk":
                        message = data.get("message", "")
                        if message:
                            parts.append(message)
                    elif action == "error":
                        err_type = data.get("type", "") or ""
                        err_status = data.get("status")
                        if err_type == "ERR_CHALLENGE":
                            raise _ChallengeError()
                        if err_type == "ERR_CONVERSATION_LIMIT":
                            raise _ConversationLimitError()
                        raise RuntimeError(
                            f"DuckDuckGo SSE error: {err_type or 'unknown'} "
                            f"(status={err_status})"
                        )
                    # start / success / unknown actions carry no text
            # Stream ended without [DONE] — treat as complete (the
            # mumu-lhl contract: message-less chunks end the stream).
            return "".join(parts), fresh_token

    def generate_stream(self, *args, **kwargs):
        """NOT IMPLEMENTED at scaffold time (FEAT-10 follow-up).

        DDG always streams at the protocol level, so a true
        incremental generator is straightforward follow-up work —
        the SSE consumer just needs to yield per-chunk instead of
        buffering. Until then, ``generate()`` covers both paths via
        internal buffering.
        """
        raise NotImplementedError(
            "DuckDuckGoBackend.generate_stream() is not implemented yet "
            "(FEAT-10) — use generate(); the backend buffers the DDG SSE "
            "stream internally."
        )

    # ─────────────────────────────────────────────────────────────────────
    # Catalog — static (DDG has no /models endpoint)
    # ─────────────────────────────────────────────────────────────────────

    def list_models(self) -> list[dict]:
        """Serve the static 5-model catalog (cached 1h, seed-backed).

        DDG has no /models endpoint — the catalog IS the seed list
        (client-verified IDs). Routed through the persistent model
        cache so `--cache-status` stays uniform; the L2 cache is
        seeded from the static list and never goes stale against a
        live source (there is none).
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
        """REACT for every DDG model — the protocol strips ``tools``.

        DDG silently drops tools/tool_choice/tool_calls from requests
        AND responses; native function calling cannot work. ReAct
        prompting (the agent loop's textual Action/Action Input path)
        is the only tool-calling mode. This is a PROTOCOL FACT, not a
        per-model probe — no cache, no live call.
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
        (chain of thought) into the response?". Even o3-mini — a
        reasoning model upstream — never surfaces CoT through the DDG
        SSE shape (only ``message`` text arrives). NO is the honest
        verdict for the whole catalog; ``--think`` has nothing to
        display on this backend.
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
        API key; DDG has none to check). The catalog is a static seed
        list, so `agentkthx models --backend duckduckgo` renders
        fully offline. Real liveness is proven by generate()'s own
        /status handshake, with the 401/ERR_CHALLENGE re-bootstrap
        and error taxonomy as the failure surface.
        """
        return True
