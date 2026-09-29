"""
⚛️ AgentKthx — Core Types
Enumeration types used throughout the framework.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Literal


class StepResultType(Enum):
    """Result type of an agent step."""

    TOOL_CALL = auto()
    FINAL_ANSWER = auto()
    ERROR = auto()
    MAX_STEPS = auto()


class ToolSupportLevel(Enum):
    """Level of tool support provided by a model.

    Detection is purely runtime-based. Each model must be tested individually
    because tool support depends on the model's template, not its family.

    Use backend.test_tool_support() for runtime detection, or check the cache
    via tool_cache.get_cached_tool_support().
    """

    NATIVE = "native"  # Native function calling support (API tool_calls)
    REACT = "react"  # Text-based tool use via ReAct prompting
    NONE = "none"  # No tool support (pure reasoning)
    UNTESTED = "untested"  # Not yet tested - each model must be tested individually

    @classmethod
    def detect(cls, model_name: str, backend=None, use_cache: bool = True) -> "ToolSupportLevel":
        """
        Get tool support level for a model.

        This method checks the cache first. If not cached, returns UNTESTED.
        For actual runtime testing, use backend.test_tool_support(force_test=True).

        Args:
            model_name: Name of the model
            backend: Optional backend for runtime testing (not used by default)
            use_cache: If True, check cache first (default: True)

        Returns:
            ToolSupportLevel (UNTESTED if not in cache)
        """
        if use_cache:
            from .tool_cache import get_cached_tool_support

            cached = get_cached_tool_support(model_name)
            if cached is not None:
                return cached

        # If backend provided and we want to test, do so
        if backend is not None and hasattr(backend, "test_tool_support"):
            return backend.test_tool_support(model_name, force_test=True)

        # Cannot determine without testing
        return cls.UNTESTED


class BackendType(Enum):
    """Supported backend types."""

    OLLAMA = "ollama"
    # R07.16: TURBOQUANT is the primary user-facing name. The binary itself
    # is still `llama-server` (llama.cpp upstream name) — TURBOQUANT_SERVER_PATH
    # env var controls its path. The LlamaServerBackend class returns this
    # value from `backend_type`, so the chat footer shows `🔌 turboquant`.
    # `LLAMA_SERVER` is kept for backward compat (any third-party code that
    # checks `backend_type == BackendType.LLAMA_SERVER` still works — but
    # the active backend_type value is now TURBOQUANT).
    TURBOQUANT = "turboquant"
    LLAMA_SERVER = "llama_server"  # deprecated alias; kept for backward compat
    BITNET = "bitnet"
    ZAI = "zai"
    OPENROUTER = "openrouter"
    GEMINI = "gemini"
    HUGGINGFACE = "huggingface"
    OPENAI = "openai"
    # R07.05: OrcaRouter — zero-markup gateway to 11 upstream providers
    # (OpenAI, Anthropic, Google, DeepSeek, Grok, Qwen, Kimi, MiniMax,
    # ZAI, Kling, BytePlus). 200+ models via OpenAI Chat-Completions API.
    # See agentkthx/plugins/orcarouter/ and docs/api/ORCAROUTER_API_TECHNICAL_REFERENCE.md.
    ORCAROUTER = "orcarouter"

    # R07.09: Mistral — La Plateforme public API at https://api.mistral.ai/v1.
    # OpenAI Chat-Completions wire format with deliberate deltas (random_seed
    # instead of seed, finish_reason "model_length" for context overflow,
    # reasoning_effort ladder includes xhigh, optional safe_prompt / prefill).
    # See agentkthx/plugins/mistral/ and docs/api/MISTRAL_API_TECHNICAL_REFERENCE.md.
    MISTRAL = "mistral"

    # Pollinations — unified gateway at https://gen.pollinations.ai/v1.
    # OpenAI-compatible aggregator (one key -> dozens of vendor families)
    # covering text, vision, images, video, TTS, embeddings behind a single
    # credential. Unique traits vs every other cloud backend: the API key is
    # OPTIONAL (legacy anonymous text surface works keyless — the natural
    # zero-config bootstrap backend), payment failure is 402 PAYMENT_REQUIRED
    # (pollen budget exhausted — must trigger fallback, never retry), and
    # model cards carry live health telemetry (success_rate) that enables
    # health-aware fallback ordering no other provider offers.
    # See agentkthx/plugins/pollinations/ and
    # docs/api/POLLINATIONS_API_TECHNICAL_REFERENCE.md.
    POLLINATIONS = "pollinations"


class ApiMode(Enum):
    """API mode for backend communication.

    - OPENRE: OpenResponses API (open spec for agentic workflows)
    - OPENAI: OpenAI Chat-Completions API
    - JEV:    System-One decision mode (Jev-compatible shape)
              Uses OpenAI Chat-Completions wire format under the hood,
              but wraps the call with a constrained decision prompt
              and parses JSON output into a {decision, probability,
              alternatives} envelope. Works with any chat-capable
              backend (Ollama, ZAI, OpenRouter, llama-server).
              Native TypeSafe Jev (api.typesafe.ai/v1/systemone) is
              NOT used — this mode emulates the Jev API shape using
              free LLMs you already have access to.

    Ollama supports both endpoints:
    - /api/chat - OpenResponses format (default)
    - /v1/chat/completions - OpenAI-compatible format
    """

    OPENRE = "openre"  # OpenResponses API (/api/chat)
    OPENAI = "openai"  # OpenAI Chat-Completions API (/v1/chat/completions)
    JEV = "jev"  # System-One decision shape, powered by any LLM


class ThinkingLevel(Enum):
    """
    Thinking / reasoning effort levels for thinking-capable models.

    Maps the user-facing --thinking CLI arg to the (think, reasoning_effort)
    tuple that backends forward to the underlying LLM.

    - OFF:    Disable thinking entirely. Fastest. Passes think=False to
              the backend. Best for JEV decisions, classification, and
              other "just answer" tasks where reasoning_content is wasted
              tokens. Drops GLM-4.5-flash latency from ~22s to ~2-3s on
              trivial decisions.
    - AUTO:   Default. Let the model decide whether to think. Passes
              think=None. Most non-thinking models ignore this entirely.
    - LOW:    Light reasoning effort. Passes think=True and
              reasoning_effort="low" (OpenAI o-series / compatible models).
    - MEDIUM: Medium reasoning effort. Passes think=True and
              reasoning_effort="medium".
    - HIGH:   Heavy reasoning effort. Passes think=True and
              reasoning_effort="high". Use sparingly — expensive.
    """

    OFF = "off"
    AUTO = "auto"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def parse_thinking_arg(level: str | ThinkingLevel | None) -> tuple[bool | None, str | None]:
    """
    Map a user-facing thinking level to (think, reasoning_effort).

    Args:
        level: One of "off", "auto", "low", "medium", "high"
               (case-insensitive), or a ThinkingLevel enum, or None.

    Returns:
        Tuple (think, reasoning_effort):
        - think: True / False / None — passed as the `think` kwarg to
                 backends that support it (Ollama, ZAI, OpenRouter).
        - reasoning_effort: "low" / "medium" / "high" / None — passed
                           as `reasoning_effort` to OpenAI-compatible
                           o-series / GLM thinking models.

    Examples:
        >>> parse_thinking_arg("off")
        (False, None)
        >>> parse_thinking_arg("auto")
        (None, None)
        >>> parse_thinking_arg("low")
        (True, "low")
        >>> parse_thinking_arg(None)  # default
        (None, None)
    """
    if level is None or level == "":
        return (None, None)

    if isinstance(level, ThinkingLevel):
        lvl = level
    else:
        try:
            lvl = ThinkingLevel(level.lower().strip())
        except ValueError:
            # Unknown level — fall back to AUTO so we don't break anything
            return (None, None)

    if lvl == ThinkingLevel.OFF:
        return (False, None)
    if lvl == ThinkingLevel.AUTO:
        return (None, None)
    if lvl == ThinkingLevel.LOW:
        return (True, "low")
    if lvl == ThinkingLevel.MEDIUM:
        return (True, "medium")
    if lvl == ThinkingLevel.HIGH:
        return (True, "high")
    return (None, None)


# Type aliases for clarity
ModelName = str
ToolName = str
MessageRole = Literal["system", "user", "assistant", "tool"]
