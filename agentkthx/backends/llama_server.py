"""
⚛️ AgentKthx — llama-server Backend
Backend for llama.cpp server (e.g., llama-cpp-turboquant fork).

Supports OpenAI Chat Completions API (/v1/chat/completions) and the
native llama.cpp /completion endpoint. Works with any llama.cpp-based
server, including the TurboQuant+ fork for KV cache compression.

Endpoints used:
  - GET  /v1/models              → model discovery
  - POST /v1/chat/completions    → OpenAI Chat Completions (tools, streaming)
  - POST /completion             → llama.cpp native (ReAct mode)
  - GET  /health                 → server health check

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Generator

from .base import BaseBackend, BackendConfig
from .ollama import OllamaBackend
from ..core.types import BackendType, ToolSupportLevel, ApiMode
from ..core.models import Tool, ToolParam
from ..config import LLAMA_SERVER_BASE_URL, BITNET_BASE_URL


class LlamaServerBackend(OllamaBackend):
    """
    Backend for llama.cpp server (llama-server).

    Inherits the full OpenAI Chat Completions implementation from OllamaBackend
    and overrides server-management endpoints (model discovery, health, model info)
    to use llama-server's API instead of Ollama's.

    Supports llama-server, llama-cpp-turboquant, BitNet, and any other llama.cpp fork
    that exposes the standard OpenAI-compatible endpoints.

    BitNet mode (--backend bitnet):
        When bitnet_mode=True, defaults to OPENRE API mode (BitNet only exposes
        /completion), uses empty stop sequences, hardcoded list_models stub, and
        always returns REACT for tool support. This preserves backward compatibility
        with the former BitNetBackend while eliminating code duplication.

    Usage:
        backend = get_backend("llama-server")
        backend = get_backend("bitnet")  # alias — bitnet_mode=True

    CLI:
        agentkthx chat --backend llama-server --base-url http://localhost:8764 --model qwen2.5:7b
        agentkthx chat --backend llama-server --api-mode openai  # OpenAI Chat Completions
        agentkthx chat --backend llama-server --api-mode openre  # Native /completion (ReAct)
        agentkthx chat --backend bitnet                     # BitNet compat mode
    """

    def __init__(
        self,
        base_url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        config: BackendConfig | None = None,
        api_mode: ApiMode | str | None = None,
        bitnet_mode: bool = False,
    ):
        # BitNet mode: default to OPENRE, use BITNET_BASE_URL
        self._bitnet_mode = bitnet_mode

        # Determine base URL - priority: base_url > host/port > env > default
        if base_url:
            resolved_url = base_url.rstrip("/")
        elif host and port:
            resolved_url = f"http://{host}:{port}"
        elif bitnet_mode:
            resolved_url = BITNET_BASE_URL.rstrip("/")
        else:
            resolved_url = LLAMA_SERVER_BASE_URL.rstrip("/")

        # Set API mode
        if api_mode is None:
            api_mode = ApiMode.OPENRE if bitnet_mode else ApiMode.OPENAI
        if isinstance(api_mode, str):
            api_mode = ApiMode(api_mode.lower())

        # Call parent (OllamaBackend → BaseBackend) with resolved values.
        # This ensures any future shared init logic is not silently skipped.
        super().__init__(
            base_url=resolved_url,
            config=config,
            api_mode=api_mode,
        )

    @property
    def backend_type(self) -> BackendType:
        return BackendType.BITNET if self._bitnet_mode else BackendType.LLAMA_SERVER

    # ─────────────────────────────────────────────────────────────────────
    # Server Management — llama-server endpoints (not Ollama)
    # ─────────────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """Check if llama-server is running via /health endpoint."""
        try:
            import urllib.request
            import urllib.error

            url = f"{self._base_url}/health"
            req = urllib.request.Request(url, method="GET")

            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status == 200

        except Exception:
            # Fallback: try root URL
            try:
                import urllib.request
                import urllib.error

                req = urllib.request.Request(self._base_url, method="GET")
                with urllib.request.urlopen(req, timeout=3) as response:
                    return response.status < 500
            except Exception:
                return False

    def list_models(self) -> list[dict]:
        """
        List available models.

        BitNet mode: queries /props to discover the loaded model name.
        BitNet is a llama.cpp fork, so /props returns model_path which we
        extract the filename from. Falls back to "bitnet" stub if the
        server doesn't support /props.

        llama-server mode: queries /v1/models for OpenAI-compatible listing.
        Falls back to /props (llama.cpp) to get the loaded model's filename
        when /v1/models returns nothing useful.
        """
        import urllib.request
        import urllib.error

        # BitNet mode: use /props to discover the loaded model
        # (BitNet typically doesn't expose /v1/models)
        if self._bitnet_mode:
            try:
                req = urllib.request.Request(
                    f"{self._base_url}/props", method="GET"
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    props = json.loads(response.read().decode("utf-8"))

                model_name = "bitnet"
                # Model path can be at top-level ("model_path") or nested
                # under "default_generation_settings.model" (some llama.cpp
                # forks, including BitNet's fork, use the nested location).
                model_path = props.get("model_path", "")
                if not model_path:
                    model_path = props.get("default_generation_settings", {}).get("model", "")
                if model_path:
                    # Extract filename: "/models/qwen2.5-0.5b.gguf" → "qwen2.5-0.5b"
                    import os.path as _osp
                    model_name = _osp.splitext(_osp.basename(model_path))[0]

                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [bitnet] list_models: discovered model='{model_name}' via /props")

                return [{
                    "name": model_name,
                    "size": 0,
                    "details": {
                        "family": "bitnet",
                        "backend": "bitnet",
                        "model_path": model_path,
                    },
                }]

            except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
                pass

            # Fallback: hardcoded stub
            return [{
                "name": "bitnet",
                "size": 0,
                "details": {"family": "bitnet", "backend": "bitnet"},
            }]

        # llama-server mode: try /v1/models first (OpenAI-compatible)
        url = f"{self._base_url}/v1/models"

        try:
            req = urllib.request.Request(url, method="GET")

            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))

            models = []
            for m in result.get("data", []):
                # R06.57: llama-server's /v1/models returns a "meta" field with
                # n_ctx (runtime context), n_ctx_train (model max), n_params,
                # size, n_vocab, n_embd, ftype. Extract these so the
                # num_ctx/32 cap uses the actual server-configured context.
                meta = m.get("meta", {})
                n_ctx = meta.get("n_ctx", 0)
                n_ctx_train = meta.get("n_ctx_train", 0)
                n_params = meta.get("n_params", 0)
                size = meta.get("size", 0)
                models.append({
                    "name": m.get("id", "unknown"),
                    "size": size,
                    "details": {
                        "family": "llama-server",
                        "backend": "llama-cpp",
                        # Store the meta fields for get_model_runtime_context
                        # and get_model_max_context to pick up.
                        "n_ctx": n_ctx,
                        "n_ctx_train": n_ctx_train,
                        "n_params": n_params,
                        "size": size,
                    },
                })

            if models:
                return models

        except (urllib.error.HTTPError, urllib.error.URLError):
            pass

        # Fallback: try /props (llama.cpp native) to get the loaded model name
        # /props returns {"model_path": "/path/to/model.gguf", ...}
        try:
            req = urllib.request.Request(
                f"{self._base_url}/props", method="GET"
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                props = json.loads(response.read().decode("utf-8"))

            model_name = "default"
            # Model path can be at top-level or nested under default_generation_settings
            model_path = props.get("model_path", "")
            if not model_path:
                model_path = props.get("default_generation_settings", {}).get("model", "")
            if model_path:
                # Extract just the filename (e.g., "/models/qwen2.5-7b-q4_k_m.gguf")
                import os.path as _osp
                model_name = _osp.splitext(_osp.basename(model_path))[0]

            return [{
                "name": model_name,
                "size": 0,
                "details": {
                    "family": "llama-server",
                    "backend": "llama-cpp",
                    "model_path": model_path,
                },
            }]

        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
            pass

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [llama-server] list_models: /v1/models and /props both failed, returning default")
        return [{
            "name": "default",
            "size": 0,
            "details": {"family": "llama-server", "backend": "llama-cpp"},
        }]

    def get_model_info(self, model: str) -> dict | None:
        """
        Get model information from llama-server.

        llama-server doesn't have an equivalent of Ollama's /api/show,
        so we return basic info from /v1/models if available.
        """
        models = self.list_models()
        for m in models:
            if m.get("name") == model or model == "default":
                return m
        return None

    # ─────────────────────────────────────────────────────────────────────
    # OpenRE mode — llama.cpp native /completion endpoint
    # ─────────────────────────────────────────────────────────────────────

    def generate(self, model: str, messages: list[dict], tools: list[Tool] | None = None,
                 temperature: float = 0.7, max_tokens: int = 2048,
                 think: bool | None = None, **kwargs) -> dict:
        """
        Generate a response from llama-server.

        Dispatches based on api_mode:
        - openai: uses /v1/chat/completions (full: tools, streaming, etc.)
        - openre: uses /completion (llama.cpp native, ReAct mode only)
        """
        if self._api_mode == ApiMode.OPENRE:
            return self._generate_completion(model, messages, tools, temperature, max_tokens, **kwargs)

        # OpenAI mode — use the inherited OllamaBackend implementation
        # which talks to /v1/chat/completions
        return super().generate(model, messages, tools, temperature, max_tokens, think=think, **kwargs)

    def generate_stream(self, model: str, messages: list[dict], tools: list[Tool] | None = None,
                        temperature: float = 0.7, max_tokens: int = 2048, **kwargs) -> Generator[str, None, None]:
        """
        Stream generated text from llama-server.

        Dispatches based on api_mode:
        - openai: uses /v1/chat/completions (streaming SSE)
        - openre: uses /completion (streaming)
        """
        if self._api_mode == ApiMode.OPENRE:
            return self._stream_completion(model, messages, tools, temperature, max_tokens, **kwargs)

        return super().generate_stream(model, messages, tools, temperature, max_tokens, **kwargs)

    def _generate_completion(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> dict:
        """
        Generate via llama.cpp native /completion endpoint (OpenRE/ReAct mode).

        This endpoint mirrors BitNet's /completion API. Tools are embedded in the
        prompt as ReAct instructions — no native tool calling support.

        NOTE: The /completion endpoint does raw completion on the prompt string —
        it has NO chat template and applies NO default stop sequences. The model
        name is not sent to the server (llama-server loads one model at startup).
        Stop tokens must be explicitly provided or the model generates until
        n_predict is exhausted.
        """
        import urllib.request
        import urllib.error

        url = f"{self._base_url}/completion"

        if os.environ.get("AGENTKTHX_DEBUG"):
            label = "bitnet" if self._bitnet_mode else "llama-server"
            print(f"  [{label}] /completion mode: model={model!r} (config-only, not sent to server)")

        # Convert messages to a single prompt (family-aware format)
        prompt = self._messages_to_prompt(messages, tools, model=model)

        # Build stop sequences: caller-provided > BitNet defaults > family defaults > turn-bleed guards
        stop_sequences = list(kwargs.get("stop", []))
        stop_sequences = [s for s in stop_sequences if s]  # filter empty strings

        # Add model-family stop tokens as a safety net (both BitNet and llama-server).
        # agent.py should send these via kwargs["stop"], but this catches
        # direct backend usage where kwargs may not include family stops.
        from ..core.model_family_config import get_model_config
        family_config = get_model_config(model)
        for family_stop in family_config.stop_tokens:
            if family_stop and family_stop not in stop_sequences:
                stop_sequences.append(family_stop)

        # Turn-bleed guard: stop the model from generating the next user/assistant turn.
        # The /completion endpoint has no chat template, so nothing prevents the model
        # from continuing into "\nUser: ..." or "\nAssistant: ..." after its answer.
        # These are cheap insurance against context confusion in multi-turn sessions.
        turn_stops = ["\nUser: ", "\nAssistant:"]
        for ts in turn_stops:
            if ts not in stop_sequences:
                stop_sequences.append(ts)

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [{label}] stop_sequences={stop_sequences}")

        body = {
            "prompt": prompt,
            "n_predict": max_tokens,
            "temperature": temperature,
            "stop": stop_sequences,
        }

        # BitNet: enable repeat penalty to prevent degenerate loops
        # (e.g., model repeating "Final Answer: 42" indefinitely).
        # 0.5b models are especially prone to repetition — 1.3 is the
        # minimum to break the cycle; 1.2 is insufficient.
        if self._bitnet_mode:
            body["repeat_penalty"] = 1.3

        start_time = time.time()

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            label = "bitnet" if self._bitnet_mode else "llama-server"
            raise RuntimeError(f"{label} HTTP error {e.code}: {error_body}")

        except (urllib.error.URLError, ConnectionError, OSError) as e:
            label = "bitnet" if self._bitnet_mode else "llama-server"
            reason = getattr(e, 'reason', str(e))
            raise RuntimeError(f"{label} connection error: {reason}")

        latency_ms = (time.time() - start_time) * 1000

        content = result.get("content", "")

        return {
            "content": content,
            "tool_calls": [],  # No native tools in /completion mode
            "usage": {
                "prompt_tokens": result.get("tokens_evaluated", 0),
                "completion_tokens": result.get("tokens_predicted", 0),
                "total_tokens": result.get("tokens_evaluated", 0) + result.get("tokens_predicted", 0),
            },
            "latency_ms": latency_ms,
            "raw": result,
        }

    def _stream_completion(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> Generator[str, None, None]:
        """
        Stream via llama.cpp native /completion endpoint (OpenRE/ReAct mode).
        """
        import urllib.request
        import urllib.error

        url = f"{self._base_url}/completion"

        prompt = self._messages_to_prompt(messages, tools, model=model)

        # Build stop sequences: caller-provided > BitNet defaults > family defaults > turn-bleed guards
        stop_sequences = list(kwargs.get("stop", []))
        stop_sequences = [s for s in stop_sequences if s]  # filter empty strings

        # Add model-family stop tokens as a safety net (same logic as _generate_completion)
        from ..core.model_family_config import get_model_config
        family_config = get_model_config(model)
        for family_stop in family_config.stop_tokens:
            if family_stop and family_stop not in stop_sequences:
                stop_sequences.append(family_stop)

        # Turn-bleed guard (same logic as _generate_completion)
        turn_stops = ["\nUser: ", "\nAssistant:"]
        for ts in turn_stops:
            if ts not in stop_sequences:
                stop_sequences.append(ts)

        body = {
            "prompt": prompt,
            "n_predict": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stop": stop_sequences,
        }

        # BitNet: enable repeat penalty to prevent degenerate loops
        if self._bitnet_mode:
            body["repeat_penalty"] = 1.3

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                for line in response:
                    if not line:
                        continue

                    try:
                        chunk = json.loads(line.decode("utf-8"))
                        content = chunk.get("content", "")
                        if content:
                            yield content

                        if chunk.get("stop"):
                            break

                    except json.JSONDecodeError:
                        continue

        except urllib.error.HTTPError as e:
            label = "bitnet" if self._bitnet_mode else "llama-server"
            raise RuntimeError(f"{label} HTTP error {e.code}")

        except (urllib.error.URLError, ConnectionError, OSError) as e:
            label = "bitnet" if self._bitnet_mode else "llama-server"
            reason = getattr(e, 'reason', str(e))
            raise RuntimeError(f"{label} connection error: {reason}")

    # Maximum prompt character budget for BitNet.
    # BitNet's degraded tokenizer (fallback 'default' pre-tokenizer) produces
    # reserved token IDs at certain positions in the token stream, crashing
    # the i2_s inference kernel. Testing showed crashes at ~320 tokens
    # (~1016 chars). Budget is set to 1024 chars with the understanding that
    # crashes are token-position-dependent (not purely character-dependent),
    # so the actual safe limit varies by content. Conversation history is
    # aggressively pruned to stay well within budget.
    _BITNET_PROMPT_BUDGET = 1024

    # Maximum number of conversation exchanges (user+assistant pairs) for BitNet.
    # Small models (0.5b) lose coherence beyond ~3-4 turns of context.
    # This hard cap ensures older turns are dropped regardless of budget.
    _BITNET_MAX_EXCHANGES = 4

    @staticmethod
    def _sanitize_for_bitnet(text: str) -> str:
        """
        Sanitize prompt text for BitNet's degraded tokenizer.

        BitNet's llama-server fork falls back to a 'default' pre-tokenizer when
        it can't match the model's gpt2 tokenizer string. This causes certain
        character sequences to produce reserved token IDs that crash the
        inference engine's i2_s (ternary/2-bit) kernel.

        Applies two layers of sanitization:
        1. Strip crash-prone patterns (markdown tables, code fences)
        2. Truncate to fit within the BitNet prompt budget

        Testing showed crashes are token-position-dependent, not purely
        character-dependent. Keeping the total prompt short avoids the
        problematic token positions entirely.
        """
        import re

        # Layer 1: Strip markdown patterns that worsen tokenization
        # Remove fenced code blocks: ```...```
        text = re.sub(r'```[\s\S]*?```', '', text)

        # Remove markdown table rows
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('|') or stripped.startswith('|--'):
                continue
            if re.match(r'^[-]+$', stripped):
                continue
            cleaned.append(line)
        text = '\n'.join(cleaned)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

        return text

    @staticmethod
    def _truncate_for_bitnet(text: str, budget: int = 0) -> str:
        """
        Truncate text to fit within BitNet's prompt budget.

        Splits by paragraphs and keeps as many complete paragraphs as
        possible within the budget. Falls back to character-level truncation
        if a single paragraph exceeds the budget.
        """
        if budget <= 0:
            budget = LlamaServerBackend._BITNET_PROMPT_BUDGET

        if len(text) <= budget:
            return text

        # Try to keep complete paragraphs
        paragraphs = text.split('\n\n')
        result = []
        total = 0
        for para in paragraphs:
            para_len = len(para) + 2  # +2 for \n\n separator
            if total + para_len > budget:
                break
            result.append(para)
            total += para_len

        if result:
            return '\n\n'.join(result)

        # Single paragraph exceeds budget — truncate at budget
        return text[:budget]

    def _messages_to_prompt(
        self,
        messages: list[dict],
        tools: list[Tool] | None = None,
        model: str | None = None,
    ) -> str:
        """
        Convert messages to a single prompt string for /completion endpoint.

        The /completion endpoint does raw completion — no chat template is applied
        server-side. The prompt format MUST match what the model was trained on.

        When model is provided and not bitnet_mode, uses the model family's
        start_tokens (e.g., ``<|im_start|>user`` for qwen2) to format the
        conversation. Falls back to generic ``User:/Assistant:`` delimiters
        for unknown families or when model is not provided.

        When bitnet_mode=True, applies BitNet-safe formatting:
        1. Sanitizes system message and tool descriptions (strip markdown)
        2. Enforces prompt budget to avoid token-position crashes
        3. Truncates tool descriptions first (preserving system prompt)
        """
        parts = []

        # Determine if the loaded model is actually a BitNet model (degraded
        # tokenizer) vs a non-BitNet model running on the BitNet backend.
        # Budgeting and sanitization are only needed for BitNet's degraded
        # tokenizer — non-BitNet models (e.g. qwen2.5) on the BitNet server
        # have proper tokenizer support and must NOT be truncated.
        from ..core.model_family_config import detect_family
        _model_family = detect_family(model) if model else None
        _is_actual_bitnet = (_model_family == "bitnet")

        # Resolve family-specific prompt tokens (for non-BitNet models)
        family_config = None
        family_start_user = "\nUser: "
        family_start_assistant = "\nAssistant: "
        family_stop = ""

        if not _is_actual_bitnet and model:
            from ..core.model_family_config import get_model_config
            family_config = get_model_config(model)
            if family_config.start_tokens:
                family_start_user = "\n" + family_config.start_tokens.get("user", "User: ")
                family_start_assistant = "\n" + family_config.start_tokens.get("assistant", "Assistant: ")
            if family_config.stop_tokens:
                family_stop = family_config.stop_tokens[0]  # primary stop token

        # Reserve budget for conversation suffix (~100 chars)
        suffix_budget = 100  # "\nUser: <query>\n\nAssistant:"
        if _is_actual_bitnet:
            system_budget = self._BITNET_PROMPT_BUDGET - suffix_budget

        # Add system message
        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
                if _is_actual_bitnet:
                    system_content = self._sanitize_for_bitnet(system_content)
                    system_content = self._truncate_for_bitnet(
                        system_content, budget=system_budget
                    )
                parts.append(system_content)
                break

        # Add tool descriptions
        if tools:
            tool_lines = ["\n\nAvailable tools:"]
            for tool in tools:
                desc = tool.description
                if _is_actual_bitnet:
                    desc = self._sanitize_for_bitnet(desc)
                tool_lines.append(f"- {tool.name}: {desc}")
            tool_lines.append("\nUse ReAct format for tool calls:")
            tool_lines.append("Action: tool_name")
            if _is_actual_bitnet and len(tools) > 0:
                # Generate per-tool examples with actual parameter names.
                # Small models (like BitNet) copy the example verbatim;
                # a generic {"param": "value"} causes wrong argument names.
                first = tools[0]
                param = first.params[0].name if first.params else "arg"
                ptype = first.params[0].type if first.params else "value"
                placeholder = '"value"' if ptype == "string" else "0"
                tool_lines.append(f'Action Input: {{{param!r}: {placeholder}}}')
            else:
                # Non-BitNet: use actual param names if available
                first = tools[0]
                if first.params:
                    p = first.params[0]
                    ph = '"value"' if p.type == "string" else "0"
                    tool_lines.append(f'Action Input: {{{p.name!r}: {ph}}}')
                else:
                    tool_lines.append('Action Input: {"param": "value"}')

            tool_section = "\n".join(tool_lines)

            if _is_actual_bitnet:
                # Calculate remaining budget after system message
                used = len(parts[0]) if parts else 0
                remaining = system_budget - used
                if remaining < len(tool_section):
                    tool_section = self._truncate_for_bitnet(
                        tool_section, budget=max(remaining, 80)
                    )

            parts.append(tool_section)

        # Add conversation (family-aware delimiters)
        if _is_actual_bitnet:
            # BitNet: budget-aware conversation history limiting.
            # The prompt budget is very tight (~1024 chars total), so we must
            # ensure conversation history doesn't push the total past the
            # token-position crash threshold. Strategy:
            # 1. Calculate remaining budget after system + tools + suffix
            # 2. Add conversation turns newest-first until budget exhausted
            # 3. Reverse to maintain chronological order
            # 4. Also enforce a hard cap on number of exchanges

            current_len = len("\n".join(parts)) + len(f"{family_start_assistant.rstrip()}")
            remaining_budget = self._BITNET_PROMPT_BUDGET - current_len

            # Collect non-system messages, grouped into exchanges
            non_system = [m for m in messages if m["role"] != "system"]

            # Group into exchanges (user+assistant pairs)
            exchanges = []
            current_exchange = []
            for msg in non_system:
                role = msg["role"]
                content = msg["content"]
                if role == "user":
                    # Start new exchange if previous one has content
                    if current_exchange:
                        exchanges.append(current_exchange)
                    current_exchange = [(role, content)]
                elif role == "assistant":
                    current_exchange.append((role, content))
                elif role == "tool":
                    current_exchange.append((role, content))
            if current_exchange:
                exchanges.append(current_exchange)

            # Keep only the most recent N exchanges
            if len(exchanges) > self._BITNET_MAX_EXCHANGES:
                exchanges = exchanges[-self._BITNET_MAX_EXCHANGES:]

            # Add turns newest-first, measuring budget as we go
            conversation_parts = []
            used_budget = 0
            for exchange in reversed(exchanges):
                exchange_text = ""
                for role, content in exchange:
                    if role == "user":
                        exchange_text += f"{family_start_user}{content}"
                    elif role == "assistant":
                        exchange_text += f"{family_start_assistant}{content}"
                    elif role == "tool":
                        exchange_text += f"\nTool Result: {content}"

                if used_budget + len(exchange_text) <= remaining_budget:
                    conversation_parts.insert(0, exchange_text)
                    used_budget += len(exchange_text)
                else:
                    # Budget exhausted — skip older exchanges
                    break

            parts.extend(conversation_parts)
        else:
            # Non-BitNet: no budget constraints, add all turns
            for msg in messages:
                role = msg["role"]
                content = msg["content"]

                if role == "system":
                    continue  # Already added
                elif role == "user":
                    parts.append(f"{family_start_user}{content}")
                elif role == "assistant":
                    parts.append(f"{family_start_assistant}{content}")
                elif role == "tool":
                    parts.append(f"\nTool Result: {content}")

        parts.append(f"{family_start_assistant.rstrip()}")

        return "\n".join(parts)

    # ─────────────────────────────────────────────────────────────────────
    # Tool Support Testing — live test via OpenAI endpoint
    # ─────────────────────────────────────────────────────────────────────

    def test_tool_support(self, model: str, family: str | None = None, force_test: bool = False) -> ToolSupportLevel:
        """
        Test model's tool support capability.

        BitNet mode: always returns REACT (no native tool support).

        llama-server mode: live test via /v1/chat/completions.
        """
        if self._bitnet_mode:
            return ToolSupportLevel.REACT

        from ..core.tool_cache import get_cached_tool_support, cache_tool_support

        api_mode = "openai"  # Always test via OpenAI endpoint

        if not force_test:
            cached = get_cached_tool_support(model, api_mode=api_mode)
            if cached is not None:
                return cached
            return ToolSupportLevel.UNTESTED

        # Test tool: Weather (simple, commonly supported)
        test_tool = Tool(
            name="get_weather",
            description="Get the current weather for a location",
            params=[ToolParam(
                name="location",
                type="string",
                description="The city and country, e.g., 'Paris, France'"
            )],
        )

        try:
            import urllib.request
            import urllib.error

            # Use /v1/chat/completions directly
            url = f"{self._base_url}/v1/chat/completions"

            body = {
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": "What's the weather like in Tokyo?"
                }],
                "tools": [test_tool.to_openai_schema()],
                "stream": False,
                "temperature": 0.0,
                "max_tokens": 200,
            }

            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))

            choices = result.get("choices", [])
            if not choices:
                support = ToolSupportLevel.REACT
                cache_tool_support(model, support, family=family or "unknown", api_mode=api_mode)
                return support

            message = choices[0].get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])

            # Native tool calls in the response → NATIVE support
            if tool_calls:
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [llama-server] Tool support: NATIVE (tool_calls={len(tool_calls)})")
                cache_tool_support(model, ToolSupportLevel.NATIVE, family=family or "unknown", api_mode=api_mode)
                return ToolSupportLevel.NATIVE

            # Check if content contains ReAct-style tool call pattern
            if content and any(kw in content.lower() for kw in ["action:", "action input:", "final answer:"]):
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [llama-server] Tool support: REACT (text-based tool pattern)")
                cache_tool_support(model, ToolSupportLevel.REACT, family=family or "unknown", api_mode=api_mode)
                return ToolSupportLevel.REACT

            # API accepted tools but model didn't use them — still REACT-capable
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [llama-server] Tool support: REACT (tools accepted, no tool calls)")
            cache_tool_support(model, ToolSupportLevel.REACT, family=family or "unknown", api_mode=api_mode)
            return ToolSupportLevel.REACT

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            error_msg = error_body.lower()

            # If the server doesn't support the tools parameter, fall back to REACT
            if "does not support" in error_msg or "invalid" in error_msg:
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [llama-server] Tool support: REACT (server rejected tools param)")
                cache_tool_support(model, ToolSupportLevel.REACT, family=family or "unknown",
                                   error=str(e), api_mode=api_mode)
                return ToolSupportLevel.REACT

            # Other errors — assume REACT
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [llama-server] Tool support: REACT (HTTP {e.code})")
            cache_tool_support(model, ToolSupportLevel.REACT, family=family or "unknown",
                               error=str(e), api_mode=api_mode)
            return ToolSupportLevel.REACT

        except Exception as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [llama-server] Tool support test failed: {e}")
            cache_tool_support(model, ToolSupportLevel.REACT, family=family or "unknown",
                               error=str(e), api_mode=api_mode)
            return ToolSupportLevel.REACT

    # ─────────────────────────────────────────────────────────────────────
    # Context size — query the running llama-server via /v1/models meta
    # ─────────────────────────────────────────────────────────────────────

    def get_model_runtime_context(self, model: str) -> int:
        """Get the runtime context window size (what the server was started with).

        R06.57: Queries ``GET /v1/models`` and reads ``meta.n_ctx`` — this is
        the ``-c`` / ``--ctx-size`` value the server was started with. Falls
        back to the ``NUM_CTX`` env var, then 4096 (llama.cpp default).

        For TurboQuant, this picks up the ``--ctx`` flag from
        ``agentkthx turbo start --ctx 8192`` automatically — no env var needed.
        """
        # Try the server's /v1/models meta.n_ctx first
        info = self.get_model_info(model)
        if info:
            details = info.get("details", {})
            n_ctx = details.get("n_ctx", 0)
            if n_ctx and n_ctx > 0:
                return n_ctx

        # Fallback: NUM_CTX env var (set by --num-ctx CLI flag or user)
        from ..config import NUM_CTX
        if NUM_CTX and NUM_CTX > 0:
            return NUM_CTX

        return 4096

    def get_model_max_context(self, model: str, family: str | None = None) -> int:
        """Get the model's maximum trained context window size.

        R06.57: Queries ``GET /v1/models`` and reads ``meta.n_ctx_train`` —
        this is the context length the model was trained on (e.g. 262144 for
        qwen3.5). Falls back to family heuristics, then 4096.
        """
        # Try the server's /v1/models meta.n_ctx_train first
        info = self.get_model_info(model)
        if info:
            details = info.get("details", {})
            n_ctx_train = details.get("n_ctx_train", 0)
            if n_ctx_train and n_ctx_train > 0:
                return n_ctx_train

        # Fallback: family-based lookup
        if family:
            ctx = self.get_context_by_family(family)
            if ctx:
                return ctx

        return 4096

    def __repr__(self) -> str:
        return f"LlamaServerBackend(url={self._base_url}, mode={self._api_mode.value})"
