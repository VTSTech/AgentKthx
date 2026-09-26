"""
⚛️ AgentKthx — OpenAI-Compatible Backend Base (ARCH-01)

Intermediate base class for backends that speak the OpenAI Chat-Completions
wire format. Sits between ``BaseBackend`` (fully abstract) and concrete
backends like ``OllamaBackend``, ``ZaiBackend``, ``OpenRouterBackend``.

Provides the shared OpenAI-compat functionality that was previously
duplicated on (or inherited from) ``OllamaBackend``:

  - **JEV dispatch** — ``generate_decision()``, ``_maybe_jev_dispatch()``,
    ``_jev_call_completions()``, and the JEV prompt/parsing helpers
    (``_serialize_state``, ``_build_jev_messages``, ``_parse_jev_response``,
    ``_JEV_SYSTEM_PROMPT``). Previously these lived on ``OllamaBackend``
    and were inherited by ZAI/OpenRouter — even though they have nothing
    to do with Ollama's native ``/api/chat`` path.

  - **Body construction** — ``_build_openai_body()`` with the ``stream``
    parameter (R06.53 PERF-02). Previously duplicated: OpenRouter had its
    own, ZAI built inline, Ollama built inline in ``generate_completions()``.

  - **Response parsing** — ``_parse_openai_response()``. Previously only
    on OpenRouterBackend as a static method.

  - **Streaming SSE** — ``generate_completions_stream()`` (PERF-01).
    Previously each backend had its own override (R06.53/R06.54) because
    the inherited OllamaBackend version built the wrong URL. The base
    class version delegates the HTTP transport to ``_iter_sse_lines()``
    which each backend implements with its own URL/auth/retry logic.

What this class does NOT provide (stays on concrete backends):

  - Native-mode generation (Ollama's ``/api/chat`` OPENRE path)
  - Model listing / discovery
  - Tool-support testing
  - Backend-specific retry/rate-limit logic
  - Backend-specific auth header construction

Class hierarchy after this refactor::

    BaseBackend (abstract)
    └── OpenAICompatibleBackend (this class — shared OpenAI-compat logic)
        ├── OllamaBackend (native + OpenAI-compat)
        │   └── LlamaServerBackend (native llama.cpp)
        ├── ZaiBackend (ZAI /api/paas/v4)
        └── OpenRouterBackend (OpenRouter /api/v1)

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Generator, Iterable, Optional

from .base import BaseBackend, BackendConfig
from ..core.types import ApiMode, ToolSupportLevel
from ..core.models import Tool


class OpenAICompatibleBackend(BaseBackend):
    """Base class for backends that speak OpenAI Chat-Completions wire format.

    Provides JEV dispatch, body construction, response parsing, and
    streaming SSE — all the shared logic that was previously duplicated
    across OllamaBackend, ZaiBackend, and OpenRouterBackend.

    R06.57 (MAINT-05): ``is_cloud = True`` here marks all concrete
    subclasses as cloud-hosted by default. ``OllamaBackend`` overrides
    back to ``False`` because it's a local server (the original parent
    before R06.55 ARCH-01 extraction). ``LlamaServerBackend`` and
    ``BitNetBackend`` inherit ``False`` through ``OllamaBackend``.

    Concrete backends must implement the abstract methods:

    Required (from BaseBackend):
        - generate(), generate_stream(), list_models(), test_tool_support()
        - backend_type, base_url

    Required (from this class):
        - _get_chat_completions_url() → full URL for /chat/completions
        - _get_auth_headers() → Authorization header dict
        - _iter_sse_lines(url, body, headers) → generator of raw SSE bytes
        - _get_model_defaults(model) → {temperature, max_tokens, context_length}
    """

    #: R06.57 (MAINT-05): Cloud-hosted by default. OllamaBackend (local
    #: server) overrides this to False; LlamaServerBackend + BitNetBackend
    #: inherit False through OllamaBackend. ZaiBackend, OpenRouterBackend,
    #: GeminiBackend inherit True.
    is_cloud: bool = True

    # ─────────────────────────────────────────────────────────────────────
    # ARCH-03 (R06.57): Shared context-length 400 recovery + num_ctx/32 cap
    # ─────────────────────────────────────────────────────────────────────
    # Lifted from OpenRouterBackend (R06.55), GeminiBackend (R06.56), and
    # ZaiBackend (R06.57) where the same logic was triplicated. Concrete
    # backends override the regex patterns below if their error message
    # format differs (Gemini uses "of" instead of "is" + "in the input"
    # instead of "of text input").
    #
    # The cap divisor (32) is empirical — see the comment in
    # ``_apply_max_tokens_cap`` for the rationale. Override on a concrete
    # backend only if you have evidence a different divisor is needed.

    #: Regex to extract the max context length from a 400 error body.
    #: OpenRouter/ZAI: "maximum context length is 262144 tokens"
    #: Gemini overrides to: "maximum context length of 1048576"
    _CONTEXT_LENGTH_MAX_PATTERN: str = r"maximum context length is (\d+) tokens"

    #: Regex to extract input tokens from a 400 error body.
    #: OpenRouter/ZAI: "85421 of text input"
    #: Gemini overrides to: "1000000 in the input"
    _CONTEXT_LENGTH_INPUT_PATTERN: str = r"(\d+) of text input"

    #: Regex to extract tool input tokens from a 400 error body. Set to
    #: ``None`` on backends whose error format doesn't separate tool input
    #: from text input (Gemini). When None, tool input is treated as 0.
    #: OpenRouter/ZAI: "305 of tool input"
    _CONTEXT_LENGTH_TOOL_PATTERN: str | None = r"(\d+) of tool input"

    #: Divisor for the ``num_ctx // N`` max_tokens cap. 32 is empirical —
    #: see ``_apply_max_tokens_cap`` docstring. Override only with evidence.
    _MAX_TOKENS_CAP_DIVISOR: int = 32

    #: Safety margin (tokens) subtracted from the calculated safe max_tokens
    #: to leave room for system prompt growth, tool definitions, response
    #: framing. Conservative but prevents re-triggering the 400 on the next
    #: agentic-loop step.
    _CONTEXT_SAFETY_MARGIN: int = 2048

    #: Floor for the calculated safe max_tokens. If input is so large that
    #: even 1K output doesn't fit, the agent needs to summarize/prune
    #: history, not reduce output further.
    _CONTEXT_SAFE_FLOOR: int = 1024

    # ─────────────────────────────────────────────────────────────────────
    # API mode property (was on OllamaBackend, now shared here)
    # ─────────────────────────────────────────────────────────────────────

    @property
    def api_mode(self) -> ApiMode:
        """Return the current API mode (OPENRE / OPENAI / JEV)."""
        return self._api_mode

    @api_mode.setter
    def api_mode(self, value: ApiMode | str) -> None:
        """Set the API mode (string or ApiMode enum)."""
        if isinstance(value, str):
            value = ApiMode(value.lower())
        self._api_mode = value

    # ─────────────────────────────────────────────────────────────────────
    # Model context defaults (ARCH-01: moved from OllamaBackend)
    # ─────────────────────────────────────────────────────────────────────

    #: Default context sizes by model family. Used as a fallback when the
    #: backend doesn't have exact per-model data (e.g. OpenRouter when the
    #: model isn't in the cache, or a new model that hasn't been seen yet).
    FAMILY_CONTEXT_DEFAULTS = {
        "qwen2": 32768,
        "qwen2.5": 32768,
        "qwen3": 32768,
        "llama3": 8192,
        "llama3.1": 131072,
        "llama3.2": 131072,
        "llama3.3": 131072,
        "mistral": 32768,
        "mixtral": 32768,
        "gemma": 8192,
        "gemma2": 8192,
        "gemma3": 32768,
        "phi3": 128000,
        "granite": 8192,
        "granitemoe": 8192,
        "smollm": 4096,
        "deepseek": 65536,
    }

    @classmethod
    def get_context_by_family(cls, family: str) -> int | None:
        """Get default context size for a model family.

        ARCH-01: Moved from OllamaBackend so all OpenAI-compat backends
        can use it as a fallback when per-model data isn't available.
        """
        if not family:
            return None
        family_lower = family.lower()
        for fam, ctx in cls.FAMILY_CONTEXT_DEFAULTS.items():
            if fam in family_lower:
                return ctx
        return None

    def get_model_runtime_context(self, model: str) -> int:
        """Get the runtime context window size for a model.

        For cloud providers (ZAI, OpenRouter), there's no separate
        "runtime" context — the context window is fixed by the model.
        This default delegates to ``get_model_max_context()``.

        OllamaBackend overrides this to return the actual ``num_ctx``
        from the Modelfile (defaults to 2048).
        """
        return self.get_model_max_context(model)

    # ─────────────────────────────────────────────────────────────────────
    # JEV System-One Decision Mode
    # ─────────────────────────────────────────────────────────────────────

    _JEV_SYSTEM_PROMPT = (
        "You are a System-One decision model. Evaluate the state and "
        "return a single calibrated decision with a confidence "
        "probability.\n\n"
        "Rules:\n"
        "- Respond with valid JSON only. No prose, no markdown fences.\n"
        "- The JSON object MUST have these keys:\n"
        "    \"decision\": <string>,\n"
        "    \"probability\": <number 0.0-1.0>,\n"
        "    \"alternatives\": <array of {\"value\": string, \"probability\": number}>\n"
        "- If choices are provided, \"decision\" MUST be exactly one of them.\n"
        "- If no choices are provided, generate a concise decision value.\n"
        "- \"alternatives\" should contain 0-3 runner-up options, sorted by\n"
        "  probability (highest first). Excluding the chosen decision.\n"
        "- The probabilities should sum to ~1.0 across decision + alternatives.\n"
        "- Be calibrated: probability reflects how confident you are that\n"
        "  your decision is the best choice for this state."
    )

    @staticmethod
    def _serialize_state(state: str | dict) -> str:
        """Render state as a string for the prompt."""
        if isinstance(state, str):
            return state
        try:
            return json.dumps(state, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(state)

    @staticmethod
    def _build_jev_messages(
        state: str | dict,
        choices: list[str] | None,
        question: str | None,
    ) -> list[dict]:
        """Build the message list for a Jev-shaped LLM call."""
        state_text = OpenAICompatibleBackend._serialize_state(state)
        prompt_question = question or "What is the best decision for this state?"

        user_lines = [f"Question: {prompt_question}", "", f"State:\n{state_text}"]
        if choices:
            user_lines += ["", "Choices (pick exactly one):"]
            for i, c in enumerate(choices, 1):
                user_lines.append(f"  {i}. {c}")
        user_lines += [
            "",
            "Return JSON now: {\"decision\": ..., \"probability\": ..., \"alternatives\": [...]}",
        ]
        return [
            {"role": "system", "content": OpenAICompatibleBackend._JEV_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(user_lines)},
        ]

    @staticmethod
    def _parse_jev_response(content: str, choices: list[str] | None) -> dict:
        """Parse the LLM's JSON output into a Jev-shaped envelope.

        Tolerates markdown fences, leading/trailing whitespace, partial
        JSON, and missing fields. If parsing fails entirely, we still
        return a valid envelope with decision = raw content.
        """
        text = content.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        decision: str = ""
        probability: float = 0.0
        alternatives: list[dict] = []
        parse_ok = False

        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                decision = str(obj.get("decision", "")).strip()
                try:
                    probability = float(obj.get("probability", 0.0))
                except (TypeError, ValueError):
                    probability = 0.0
                probability = max(0.0, min(1.0, probability))
                raw_alts = obj.get("alternatives", []) or []
                if isinstance(raw_alts, list):
                    for alt in raw_alts:
                        if isinstance(alt, dict):
                            v = str(alt.get("value", "")).strip()
                            try:
                                p = float(alt.get("probability", 0.0))
                            except (TypeError, ValueError):
                                p = 0.0
                            if v:
                                alternatives.append({"value": v, "probability": max(0.0, min(1.0, p))})
                        elif isinstance(alt, str):
                            alternatives.append({"value": alt, "probability": 0.0})
                parse_ok = True
        except (json.JSONDecodeError, ValueError):
            parse_ok = False

        if not parse_ok:
            decision = text[:500] if text else "(empty)"
            probability = 0.0
            alternatives = []

        if choices and decision:
            decision_lower = decision.lower()
            for c in choices:
                if c.lower() == decision_lower:
                    decision = c
                    break
                if c.lower() in decision_lower or decision_lower in c.lower():
                    decision = c
                    break
            else:
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [JEV] Decision '{decision}' not in choices {choices}")

        return {
            "decision": decision,
            "probability": probability,
            "alternatives": alternatives,
            "_parse_ok": parse_ok,
        }

    def generate_decision(
        self,
        model: str,
        state: str | dict,
        choices: list[str] | None = None,
        *,
        question: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 512,
        think: bool | None = None,
        **kwargs,
    ) -> dict:
        """Evaluate a state and return a Jev-shaped decision envelope.

        Wraps the underlying chat-completions call with a constrained
        decision prompt and JSON output mode, then parses the response
        into a {decision, probability, alternatives, usage} dict.

        This is the System-One primitive. Use it directly from Python
        for routing, classification, scoring inside agent pipelines.

        Args:
            model: Model name (e.g. "glm-4.5-flash", "qwen2.5:0.5b").
            state: State to evaluate. String or dict (dict → JSON-serialized).
            choices: Optional constrained choice set. The model MUST
                    pick one of these.
            question: Optional framing question.
            temperature: Low (0.1 default) for calibrated decisions.
            max_tokens: Output budget (default 512).
            think: Forwarded to underlying LLM for thinking models.
            **kwargs: Passed through to _jev_call_completions().

        Returns:
            {
                "decision": str,
                "probability": float,
                "alternatives": [{"value": str, "probability": float}, ...],
                "usage": {"input_tokens": int, "output_tokens": int, "total_tokens": int},
                "latency_ms": float,
                "raw": dict,
                "_jev": True,
            }
        """
        messages = self._build_jev_messages(state, choices, question)

        response_format = kwargs.pop("response_format", None) or {"type": "json_object"}

        start_time = time.time()
        result = self._jev_call_completions(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            think=think,
            response_format=response_format,
            **kwargs,
        )
        latency_ms = (time.time() - start_time) * 1000

        content = result.get("content", "")
        parsed = self._parse_jev_response(content, choices)

        usage_raw = result.get("usage", {})
        usage = {
            "input_tokens": usage_raw.get("prompt_tokens", 0),
            "output_tokens": usage_raw.get("completion_tokens", 0),
            "total_tokens": usage_raw.get("total_tokens", 0),
        }

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [JEV] parse_ok={parsed['_parse_ok']}")
            print(f"  [JEV] decision={parsed['decision']!r} p={parsed['probability']}")
            print(f"  [JEV] alternatives={parsed['alternatives']}")

        reasoning_content = result.get("reasoning_content", "") or ""

        return {
            "decision": parsed["decision"],
            "probability": parsed["probability"],
            "alternatives": parsed["alternatives"],
            "usage": usage,
            "latency_ms": latency_ms,
            "reasoning_content": reasoning_content,
            "raw": result,
            "_jev": True,
            "_parse_ok": parsed["_parse_ok"],
        }

    def _maybe_jev_dispatch(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int = 8192,
        think: bool | None = None,
        **kwargs,
    ) -> dict | None:
        """Dispatch helper for JEV api_mode.

        Returns a generate()-shaped dict (with the decision stuffed into
        ``content`` as JSON) if ``self._api_mode == ApiMode.JEV``, else
        ``None`` so the caller can continue with its normal path.
        """
        if self._api_mode != ApiMode.JEV:
            return None

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [{self.__class__.__name__}] Dispatching to JEV decision mode")

        state_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                state_text = msg.get("content", "")
                break

        decision = self.generate_decision(
            model=model,
            state=state_text,
            temperature=temperature,
            max_tokens=max_tokens,
            think=think,
            **kwargs,
        )

        return {
            "content": json.dumps(decision, ensure_ascii=False),
            "tool_calls": [],
            "usage": {
                "prompt_tokens": decision.get("usage", {}).get("input_tokens", 0),
                "completion_tokens": decision.get("usage", {}).get("output_tokens", 0),
                "total_tokens": decision.get("usage", {}).get("total_tokens", 0),
            },
            "latency_ms": decision.get("latency_ms", 0.0),
            "reasoning_content": decision.get("reasoning_content", ""),
            "raw": decision,
            "_jev": True,
        }

    def _jev_call_completions(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int = 512,
        think: bool | None = None,
        response_format: dict | None = None,
        **kwargs,
    ) -> dict:
        """Hook: underlying chat-completions call used by generate_decision().

        Default implementation is a placeholder that raises
        NotImplementedError. Concrete backends override this to route
        through their own auth-injected endpoint:

        - OllamaBackend: ``/v1/chat/completions`` (no auth)
        - ZaiBackend: ``/api/paas/v4/chat/completions`` (Bearer token)
        - OpenRouterBackend: ``_make_api_request`` (Bearer + retry)
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _jev_call_completions() "
            "to support ApiMode.JEV."
        )

    # ─────────────────────────────────────────────────────────────────────
    # OpenAI Chat-Completions body construction & response parsing
    # ─────────────────────────────────────────────────────────────────────

    def _build_openai_body(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None,
        temperature: float,
        max_tokens: int,
        stream: bool = False,
        **kwargs,
    ) -> dict:
        """Build an OpenAI Chat-Completions request body.

        Centralises request construction so generate() and
        generate_completions_stream() stay in sync. All optional fields
        are only added when supplied.

        When ``stream=True``, sets ``stream_options.include_usage=True``
        so the provider emits a final SSE chunk carrying token-usage
        stats (PERF-02). Without this, streaming responses report
        ``usage=None`` and the agent loop can't track token consumption.
        """
        body: dict = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # PERF-02: when streaming, ask the provider to include usage in
        # the final SSE chunk. OpenAI, OpenRouter, and ZAI all honor this.
        if stream:
            body["stream_options"] = {"include_usage": True}

        if tools:
            body["tools"] = [t.to_openai_schema() for t in tools]

        # Optional fields — only added when explicitly provided.
        optional_int_fields = ("top_p", "top_k", "seed", "n")
        optional_float_fields = ("presence_penalty", "frequency_penalty")
        for field in optional_int_fields + optional_float_fields:
            val = kwargs.get(field)
            if val is not None:
                body[field] = val

        stop = kwargs.get("stop")
        if stop is not None:
            body["stop"] = stop if isinstance(stop, list) else [stop]

        response_format = kwargs.get("response_format")
        if response_format is not None:
            body["response_format"] = response_format

        tool_choice = kwargs.get("tool_choice")
        if tool_choice is not None:
            body["tool_choice"] = tool_choice

        # Forward reasoning_effort for thinking models (GLM-5.x, o-series)
        reasoning_effort = kwargs.get("reasoning_effort")
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort

        return body

    @staticmethod
    def _parse_openai_response(raw_response: dict) -> dict:
        """Parse an OpenAI-format Chat Completions response.

        Returns a dict in the shape AgentKthx's agent loop expects:
        {
          "content": str,
          "tool_calls": [{"id", "name", "arguments": dict}, ...],
          "finish_reason": str | None,
          "usage": {...},
          "reasoning_content": str,
          "raw": <original response>,
        }

        Raises:
            RuntimeError: if the response body carries a provider-side
                ``error`` field (happens on HTTP 200 when an upstream
                provider is rate-limited or fails). Surfacing it here
                lets the chat loop print a meaningful message instead
                of silently showing an empty response.
        """
        # Provider-side error on HTTP 200 (e.g. "Provider rate limited")
        err_field = raw_response.get("error")
        if err_field:
            if isinstance(err_field, dict):
                err_msg = err_field.get("message") or str(err_field)
                err_code = err_field.get("code")
            else:
                err_msg = str(err_field)
                err_code = None
            code_str = f" (code={err_code})" if err_code is not None else ""
            raise RuntimeError(f"Provider error: {err_msg}{code_str}")

        choices = raw_response.get("choices", []) or []
        if not choices:
            raise RuntimeError("Response returned no choices")

        choice = choices[0]
        message = choice.get("message", {}) or {}

        content = message.get("content") or ""
        raw_tool_calls = message.get("tool_calls") or []
        finish_reason = choice.get("finish_reason")
        reasoning_content = message.get("reasoning_content", "") or ""

        # Parse OpenAI tool_calls format:
        #   { "id": "...", "type": "function",
        #     "function": { "name": "...", "arguments": "<JSON string>" } }
        parsed_tool_calls: list[dict] = []
        for tc in raw_tool_calls:
            func = tc.get("function", {}) or {}
            args = func.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    args = {"_raw_arguments": args}
            parsed_tool_calls.append({
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
                "arguments": args,
            })

        usage = raw_response.get("usage", {}) or {}

        return {
            "content": content,
            "tool_calls": parsed_tool_calls,
            "finish_reason": finish_reason,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            "reasoning_content": reasoning_content,
            "raw": raw_response,
        }

    # ─────────────────────────────────────────────────────────────────────
    # Streaming (PERF-01)
    # ─────────────────────────────────────────────────────────────────────

    def generate_completions_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_p: float | None = None,
        think: bool | None = None,
        stop: str | list[str] | None = None,
        presence_penalty: float | None = None,
        frequency_penalty: float | None = None,
        response_format: dict | None = None,
        reasoning_effort: str | None = None,
        num_ctx: int | None = None,
        num_predict: int | None = None,
        truncation: str | None = None,
        **kwargs,
    ) -> Generator[dict, None, None]:
        """Stream OpenAI Chat-Completions chunks from the provider.

        ARCH-01: This is the shared streaming implementation. Each
        concrete backend provides ``_get_chat_completions_url()``,
        ``_get_auth_headers()``, and ``_iter_sse_lines()`` — this base
        method handles body construction, SSE parsing, and dict-shape
        normalization so the same code doesn't need to be duplicated on
        every backend.

        Yields dicts in the shape ``Agent._generate_stream()`` expects:
        {
          "delta": str,
          "tool_calls": list[dict] | None,
          "finish_reason": str | None,
          "reasoning_content": str | None,
        }
        """
        # Resolve per-model defaults
        defaults = self._get_model_defaults(model)
        if temperature is None:
            temperature = defaults["temperature"]
        if max_tokens is None:
            max_tokens = defaults["max_tokens"]

        # ROB-06: If a context-safe max_tokens was persisted from a
        # previous context-length 400 error, use it instead of whatever
        # was passed. This overrides both explicit max_tokens from the
        # agent and model_config defaults, because the persisted value
        # was calculated from the actual token counts in the error
        # message and is the only value that won't re-trigger the 400.
        # The attribute is set by OpenRouterBackend._iter_sse_lines()
        # after a context-length 400. Backends that don't set it
        # (Ollama, ZAI) don't have this attribute — use getattr.
        context_safe = getattr(self, "_context_safe_max_tokens", None)
        if context_safe is not None:
            max_tokens = context_safe

        # Build body with stream=True so stream_options.include_usage is sent
        body = self._build_openai_body(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            top_p=top_p,
            stop=stop,
            presence_penalty=presence_penalty,
            frequency_penalty=frequency_penalty,
            response_format=response_format,
            reasoning_effort=reasoning_effort,
            **kwargs,
        )

        url = self._get_chat_completions_url()
        headers = self._get_auth_headers()

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [{self.__class__.__name__}-Stream] POST {url} — "
                  f"tools={len(tools) if tools else 0}, stream=True")

        # Each backend's _iter_sse_lines handles its own HTTP transport
        # (urllib direct, urllib with retry, etc.) and yields raw SSE
        # line bytes. This method parses them uniformly.
        for line_bytes in self._iter_sse_lines(url, body, headers):
            if not line_bytes:
                continue
            line_str = line_bytes.decode("utf-8") if isinstance(line_bytes, bytes) else line_bytes
            if not line_str.startswith("data: "):
                continue
            json_str = line_str[6:].strip()
            if json_str == "[DONE]":
                break
            try:
                chunk = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            choices = chunk.get("choices", []) or []
            if not choices:
                # Final usage-only chunk (no choices) — capture usage data
                # for token tracking. This chunk arrives when
                # stream_options.include_usage=True (PERF-02).
                chunk_usage = chunk.get("usage")
                if chunk_usage and isinstance(chunk_usage, dict):
                    yield {"delta": "", "tool_calls": None,
                           "finish_reason": None, "_usage": chunk_usage}
                continue
            choice = choices[0]
            delta = choice.get("delta", {}) or {}
            text_delta = delta.get("content", "") or ""
            tool_calls_delta = delta.get("tool_calls")
            reasoning_delta = delta.get("reasoning_content", "") or ""
            finish_reason = choice.get("finish_reason")

            yield_chunk: dict = {
                "delta": text_delta,
                "tool_calls": tool_calls_delta,
                "finish_reason": finish_reason,
            }
            if reasoning_delta:
                yield_chunk["reasoning_content"] = reasoning_delta
            yield yield_chunk

    # ─────────────────────────────────────────────────────────────────────
    # Abstract hooks — each concrete backend must implement these
    # ─────────────────────────────────────────────────────────────────────

    def _get_chat_completions_url(self) -> str:
        """Return the full URL for the /chat/completions endpoint.

        Examples:
            OllamaBackend:  ``http://localhost:11434/v1/chat/completions``
            ZaiBackend:     ``https://api.z.ai/api/paas/v4/chat/completions``
            OpenRouterBackend: ``https://openrouter.ai/api/v1/chat/completions``
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _get_chat_completions_url()"
        )

    def _get_auth_headers(self) -> dict:
        """Return auth headers for /chat/completions requests.

        Examples:
            OllamaBackend:  ``{}`` (no auth)
            ZaiBackend:     ``{"Authorization": "Bearer <key>"}``
            OpenRouterBackend: ``{"Authorization": "Bearer <key>",
                                 "HTTP-Referer": "...", "X-Title": "..."}``
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _get_auth_headers()"
        )

    def _iter_sse_lines(
        self,
        url: str,
        body: dict,
        headers: dict,
    ) -> Iterable[bytes]:
        """Make the streaming HTTP POST and yield raw SSE line bytes.

        Each backend implements its own HTTP transport here:
        - OllamaBackend: ``urllib.request.urlopen`` direct
        - ZaiBackend: ``urllib.request.urlopen`` with error recovery
          (insufficient credits → free fallback, no-tools → ReAct)
        - OpenRouterBackend: ``urllib.request.urlopen`` with 429 retry

        Yields:
            Raw bytes lines from the SSE stream (e.g. ``b'data: {...}\\n'``).
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _iter_sse_lines()"
        )

    def _get_model_defaults(self, model: str) -> dict:
        """Return per-model defaults: ``{"temperature": float, "max_tokens": int}``."""
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _get_model_defaults()"
        )

    # ─────────────────────────────────────────────────────────────────────
    # ARCH-03 (R06.57): Shared context-length 400 recovery helpers
    # ─────────────────────────────────────────────────────────────────────
    # These three methods consolidate the previously-triplicated logic from
    # OpenRouterBackend (R06.55), GeminiBackend (R06.56), and ZaiBackend
    # (R06.57). Concrete backends override the regex patterns
    # (``_CONTEXT_LENGTH_MAX_PATTERN`` etc.) and call these helpers from
    # ``_get_model_defaults`` and ``_iter_sse_lines`` — eliminating ~400
    # lines of duplicated code and the maintenance burden of keeping three
    # copies in sync.

    def _apply_max_tokens_cap(
        self,
        max_tokens: int,
        context_length: int,
        temperature: float,
    ) -> dict:
        """Return a model-defaults dict with the ``num_ctx / 32`` cap applied.

        ARCH-03 (R06.57): Lifted from OpenRouterBackend/GeminiBackend/ZaiBackend
        where the same cap logic was triplicated. Two layers:

        1. **Persisted safe value wins**: if ``self._context_safe_max_tokens``
           is set (by ``_handle_context_length_400`` after a previous 400),
           use it directly. This value was calculated from the actual token
           counts in the error message and is the only value that won't
           re-trigger the 400.
        2. **Proactive cap**: ``min(max_tokens, context_length // 32)``.
           Many cloud models report ``max_completion_tokens`` close to the
           full ``context_length`` (e.g. OpenRouter :free models with 256K
           context report 256K max output), leaving no room for input on
           long agentic runs. ``num_ctx / 32`` (8K on a 256K model) gives
           97% of context to input.

           EMPIRICAL FINDING (R06.55 testing on nex-agi/nex-n2.5-mini:free,
           256K context, codebase-audit skill with 30+ tool calls):
             - num_ctx/4  (75%)  → 400 on step 5  (input ~85K + output ~196K)
             - num_ctx/8  (12.5%)→ 400 on step 9  (input grew to ~100K)
             - num_ctx/16 (6%)   → proceeded to step 27+ without 400
             - num_ctx/32 (3%)   → conservative default for longest tasks

           The reactive ``_handle_context_length_400`` still fires if this
           proves too large.

        Override ``_MAX_TOKENS_CAP_DIVISOR`` on a concrete backend only if
        you have evidence a different divisor is needed.
        """
        if getattr(self, "_context_safe_max_tokens", None) is not None:
            return {
                "temperature": temperature,
                "max_tokens": self._context_safe_max_tokens,
                "context_length": context_length,
            }
        capped = min(max_tokens, context_length // self._MAX_TOKENS_CAP_DIVISOR)
        return {
            "temperature": temperature,
            "max_tokens": capped,
            "context_length": context_length,
        }

    def _calculate_safe_max_tokens(self, error_body: str, body: dict) -> int | None:
        """Parse a context-length 400 error and compute a safe max_tokens.

        Uses the per-backend regex patterns (``_CONTEXT_LENGTH_MAX_PATTERN``,
        ``_CONTEXT_LENGTH_INPUT_PATTERN``, ``_CONTEXT_LENGTH_TOOL_PATTERN``)
        to extract token counts from the error message, then computes:

            safe_max = max_context - input_tokens - tool_tokens - safety_margin

        Floors at ``_CONTEXT_SAFE_FLOOR`` (1024). Returns ``None`` if the
        computed value is >= the current ``body["max_tokens"]`` (already safe,
        something else is wrong) or if the regex can't parse the message
        (caller falls back to a 1/3 reduction).

        ARCH-03 (R06.57): Lifted from OpenRouterBackend/GeminiBackend/ZaiBackend.
        """
        import re
        text = error_body.lower()

        max_match = re.search(self._CONTEXT_LENGTH_MAX_PATTERN, text)
        input_match = re.search(self._CONTEXT_LENGTH_INPUT_PATTERN, text)
        tool_match = (
            re.search(self._CONTEXT_LENGTH_TOOL_PATTERN, text)
            if self._CONTEXT_LENGTH_TOOL_PATTERN
            else None
        )

        if not max_match or not input_match:
            # Can't parse — fall back to 1/3 reduction (conservative)
            old_max = body.get("max_tokens", 4096)
            new_max = max(old_max // 3, 4096)
            return new_max if new_max < old_max else None

        max_context = int(max_match.group(1))
        input_tokens = int(input_match.group(1))
        if tool_match:
            input_tokens += int(tool_match.group(1))

        safe_max = max_context - input_tokens - self._CONTEXT_SAFETY_MARGIN
        if safe_max < self._CONTEXT_SAFE_FLOOR:
            safe_max = self._CONTEXT_SAFE_FLOOR

        old_max = body.get("max_tokens", 4096)
        if safe_max >= old_max:
            return None  # already safe, something else is wrong
        return safe_max

    def _handle_context_length_400(self, error_body: str, body: dict) -> bool:
        """Parse a context-length 400, persist safe max_tokens, mutate body.

        Returns ``True`` if the caller should retry the request with the
        updated ``body``, ``False`` if the error isn't a context-length 400
        or no safe value could be computed (caller should raise).

        Side effects:
        - Sets ``self._context_safe_max_tokens`` so future
          ``_get_model_defaults`` / ``_apply_max_tokens_cap`` calls reuse
          the safe value (prevents the death-spiral of repeated 400s).
        - Mutates ``body["max_tokens"]`` in place for the retry.

        ARCH-03 (R06.57): Lifted from OpenRouterBackend/GeminiBackend/ZaiBackend
        ``_iter_sse_lines`` methods where the same parse/persist/retry dance
        was triplicated. Concrete backends call this from their
        ``_iter_sse_lines`` 400 branch.
        """
        if "context length" not in error_body.lower():
            return False
        new_max = self._calculate_safe_max_tokens(error_body, body)
        if new_max is None:
            return False
        body["max_tokens"] = new_max
        self._context_safe_max_tokens = new_max
        return True
