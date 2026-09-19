"""
⚛️ AgentNova — Base Backend
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
    retry_delay: float = 1.0
    truncation: str = "auto"


class BaseBackend(ABC):
    """
    Abstract base class for inference backends.

    All backends must implement:
    - generate(): Generate text from messages
    - generate_stream(): Stream generated text
    - list_models(): List available models
    - test_tool_support(): Test model's tool support capability
    """

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

    def count_tokens(self, text: str) -> int:
        """
        Estimate token count for text.

        This is a simple estimation. Backends may override
        for more accurate counting.

        Args:
            text: Text to count

        Returns:
            Estimated token count
        """
        # Simple estimation: ~4 characters per token
        return len(text) // 4

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(url={self.base_url})"
