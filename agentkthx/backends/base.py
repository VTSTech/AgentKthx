"""
⚛️ AgentKthx — Base Backend
Abstract base class for inference backends.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Generator, Optional

from ..core.types import BackendType, ToolSupportLevel
from ..core.models import Tool


@dataclass
class BackendConfig:
    """Configuration for a backend."""
    host: str = "localhost"
    port: int = 11434
    timeout: int = 120
    max_retries: int = 3
    truncation: str = "auto"


class BaseBackend(ABC):
    """
    Abstract base class for inference backends.

    All backends must implement:
    - generate(): Generate text from messages
    - generate_stream(): Stream generated text
    - list_models(): List available models
    - test_tool_support(): Test model's tool support capability

    R06.57 (MAINT-05): ``is_cloud`` class attribute distinguishes cloud-hosted
    backends (ZAI, OpenRouter, Gemini — remote API, billed, rate-limited,
    OpenAI-compat only) from local backends (Ollama, llama-server, BitNet —
    local server, native /api/chat support, no rate limits). The CLI uses
    this to decide default streaming, catalog-based defaults, column layout
    in ``cmd_models``, and default api_mode — without hardcoding backend
    names or BackendType values. Adding a 5th cloud backend is now a
    1-line change (subclass OpenAICompatibleBackend) instead of an 8-site
    edit across cli.py.
    """

    #: R06.57 (MAINT-05): True for cloud-hosted backends. Override on
    #: concrete local backends (OllamaBackend sets False). Defaults to
    #: False here so any future BaseBackend subclass that doesn't go
    #: through OpenAICompatibleBackend is treated as local — safer than
    #: defaulting to True (would silently enable cloud behaviors for
    #: unknown backends).
    is_cloud: bool = False

    def __init__(
        self,
        config: BackendConfig | None = None,
        base_url: str | None = None,
        api_mode: object | None = None,
    ):
        self.config = config or BackendConfig()
        self._base_url = base_url.rstrip("/") if base_url else None
        self._api_mode = api_mode  # Subclasses should set to ApiMode enum

    @property
    @abstractmethod
    def backend_type(self) -> BackendType:
        """Return the backend type."""
        pass

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Return the base URL for the backend."""
        pass

    @abstractmethod
    def generate(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 8192,
        **kwargs,
    ) -> dict:
        """
        Generate a response from the model.

        Args:
            model: Model name
            messages: Conversation messages
            tools: Available tools (optional)
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Additional model-specific parameters

        Returns:
            Response dict with at least:
            - content: Generated text
            - tool_calls: List of tool calls (if any)
            - usage: Token usage dict
        """
        pass

    @abstractmethod
    def generate_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 8192,
        **kwargs,
    ) -> Generator[str, None, None]:
        """
        Stream generated text.

        Args:
            model: Model name
            messages: Conversation messages
            tools: Available tools (optional)
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Additional model-specific parameters

        Yields:
            Text chunks as they're generated
        """
        pass

    @abstractmethod
    def list_models(self) -> list[dict]:
        """
        List available models.

        Returns:
            List of model info dicts
        """
        pass

    @abstractmethod
    def test_tool_support(self, model: str, family: str | None = None, force_test: bool = False) -> ToolSupportLevel:
        """
        Test a model's tool support capability.

        Args:
            model: Model name
            family: Optional family name (for fast detection)
            force_test: If True, always make a test API call

        Returns:
            Detected ToolSupportLevel
        """
        pass

    # ─────────────────────────────────────────────────────────────────────
    # System-One Decision Mode (ApiMode.JEV)
    # ─────────────────────────────────────────────────────────────────────
    # generate_decision() is the System-One counterpart to generate().
    # Instead of returning free-form text, it returns a structured
    # decision envelope compatible with the Jev API shape:
    #
    #   {
    #     "decision": str,                # chosen option (or generated answer)
    #     "probability": float,           # 0.0-1.0 calibrated confidence
    #     "alternatives": [               # ranked runner-ups
    #       {"value": str, "probability": float}, ...
    #     ],
    #     "usage": {                      # token accounting
    #       "input_tokens": int,
    #       "output_tokens": int,
    #       "total_tokens": int,
    #     },
    #     "latency_ms": float,
    #     "raw": dict,                    # underlying LLM response (debug)
    #     "_jev": True,                   # marker for downstream code
    #   }
    #
    # The default implementation uses TypeSafe's "System One LLM wrapper"
    # pattern: it wraps any chat-capable LLM with a constrained decision
    # prompt + JSON output mode, then parses the JSON envelope.
    #
    # Backends that ship a native System-One model (e.g. a future Jev
    # plugin hitting api.typesafe.ai/v1/systemone) may override this to
    # skip the LLM wrapper and call the decision endpoint directly.
    # ─────────────────────────────────────────────────────────────────────

    def generate_decision(
        self,
        model: str,
        state: str | dict,
        choices: list[str] | None = None,
        *,
        question: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 512,
        **kwargs,
    ) -> dict:
        """
        Evaluate a state and return a typed decision + probability envelope.

        This is the System-One primitive: fast, structured, calibrated.
        Use it for routing, classification, scoring, threshold checks,
        or any "just decide this" call site inside an agent pipeline.

        Args:
            model: Underlying LLM model name (e.g. "glm-4.5-flash").
            state: The state to evaluate. Either a string (freeform
                   description) or a dict (structured state — will be
                   JSON-serialized in the prompt).
            choices: Optional constrained choice set. When provided,
                    the model MUST pick one of these. When omitted, the
                    model generates a freeform decision.
            question: Optional framing question. Defaults to "What is
                      the best decision for this state?".
            temperature: Low temperature (default 0.1) for calibrated
                       decisions. Higher values give more varied
                       alternatives.
            max_tokens: Output budget (default 512). Decisions are
                       short, so this can stay small.
            **kwargs: Passed through to the underlying LLM call.

        Returns:
            Decision envelope dict (see class docstring above).

        Raises:
            NotImplementedError: If the backend has no chat-completions
                               endpoint to wrap (e.g. a pure OPENRE
                               backend that can't emit JSON).
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement generate_decision(). "
            "ApiMode.JEV requires a backend that can emit JSON via OpenAI "
            "Chat-Completions (ollama, zai, openrouter, llama-server)."
        )

    def is_running(self) -> bool:
        """Check if the backend is running."""
        try:
            import urllib.request
            import urllib.error

            url = f"{self.base_url}/api/version"
            req = urllib.request.Request(url, method="GET")

            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status == 200

        except Exception:
            return False

    def get_model_info(self, model: str) -> dict | None:
        """
        Get information about a specific model.

        Args:
            model: Model name

        Returns:
            Model info dict or None
        """
        models = self.list_models()
        for m in models:
            if m.get("name") == model:
                return m
        return None

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(url={self.base_url})"
