"""
⚛️ AgentKthx — Central Configuration

Single source of truth for core framework configuration.
Plugin-owned config (BitNet, ZAI, ACP, TurboQuant) is read from
environment variables and defaults are defined in each plugin's
plugin.json manifest.  The module-level variables below are kept for
backward compatibility — they simply read from the environment.

Status: Alpha

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse


# ═══════════════════════════════════════════════════════════════════════════════
# OLLAMA CONFIGURATION (native backend)
# ═══════════════════════════════════════════════════════════════════════════════
# Default for local Ollama
OLLAMA_BASE_URL = "http://localhost:11434"

# Override via environment variable (takes precedence if set)
_ollama_env = os.environ.get("OLLAMA_BASE_URL")
if _ollama_env:
    OLLAMA_BASE_URL = _ollama_env


# ═══════════════════════════════════════════════════════════════════════════════
# LLAMA-SERVER CONFIGURATION (native backend)
# ═══════════════════════════════════════════════════════════════════════════════
# Default for local llama-server (native backend, always available)
LLAMA_SERVER_BASE_URL = "http://localhost:8764"

# Override via environment variable
_llama_server_env = os.environ.get("LLAMA_SERVER_BASE_URL")
if _llama_server_env:
    LLAMA_SERVER_BASE_URL = _llama_server_env


# ═══════════════════════════════════════════════════════════════════════════════
# PLUGIN-OWNED CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════
# The following config variables are owned by their respective plugins
# (bitnet, zai, acp, turboquant).  They read from environment variables
# with defaults defined in each plugin's plugin.json manifest.
# Kept here for backward compatibility — plugin code imports these.

# BitNet plugin (agentnova/plugins/bitnet/)
BITNET_BASE_URL = os.environ.get("BITNET_TUNNEL") or os.environ.get("BITNET_BASE_URL", "http://localhost:8765")

# ZAI plugin (agentnova/plugins/zai/)
ZAI_BASE_URL = os.environ.get("ZAI_BASE_URL", "https://api.z.ai")
ZAI_API_KEY = os.environ.get("ZAI_API_KEY", "")
ZAI_FREE_ONLY = os.environ.get("ZAI_FREE_ONLY", "").lower() in ("1", "true", "yes")
ZAI_FREE_FALLBACK_MODEL = os.environ.get("ZAI_FREE_FALLBACK_MODEL", "glm-4.5-flash")

# ACP plugin (agentnova/plugins/acp/)
ACP_BASE_URL = os.environ.get("ACP_BASE_URL", "http://localhost:8766")
ACP_USER = os.environ.get("ACP_USER", "admin")
ACP_PASS = os.environ.get("ACP_PASS", "secret")

# TurboQuant plugin (agentnova/plugins/turboquant/)
TURBOQUANT_SERVER_PATH = os.environ.get("TURBOQUANT_SERVER_PATH", "llama-server")
TURBOQUANT_PORT = int(os.environ.get("TURBOQUANT_PORT", "8764"))
TURBOQUANT_CTX = int(os.environ.get("TURBOQUANT_CTX", "8192"))

# OpenRouter plugin (agentnova/plugins/openrouter/)
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_DEFAULT_MODEL = os.environ.get("OPENROUTER_DEFAULT_MODEL", "anthropic/claude-3.5-sonnet")
OPENROUTER_FREE_ONLY = os.environ.get("OPENROUTER_FREE_ONLY", "").lower() in ("1", "true", "yes")


# ═══════════════════════════════════════════════════════════════════════════════
# BACKEND SELECTION
# ═══════════════════════════════════════════════════════════════════════════════
# Set AGENTNOVA_BACKEND to select a backend.
# Accept any value — plugin backends are loaded lazily via PluginManager.
# Default: "ollama"
AGENTNOVA_BACKEND = os.environ.get("AGENTNOVA_BACKEND", "ollama").lower()


# ═══════════════════════════════════════════════════════════════════════════════
# DEFAULT MODEL
# ═══════════════════════════════════════════════════════════════════════════════
# Default model for tests and examples
# BitNet default: bitnet-b1.58-2b-4t
# Ollama default: qwen2.5-coder:0.5b-instruct-q4_k_m
# ZAI default: glm-5.1
# OpenRouter default: anthropic/claude-3.5-sonnet
if AGENTNOVA_BACKEND == "bitnet":
    DEFAULT_MODEL = os.environ.get("AGENTNOVA_MODEL", "bitnet-b1.58-2b-4t")
elif AGENTNOVA_BACKEND in ("llama-server", "llama_server"):
    DEFAULT_MODEL = os.environ.get("AGENTNOVA_MODEL", "default")
elif AGENTNOVA_BACKEND == "zai":
    DEFAULT_MODEL = os.environ.get("AGENTNOVA_MODEL", "glm-5.1")
elif AGENTNOVA_BACKEND == "openrouter":
    DEFAULT_MODEL = os.environ.get("AGENTNOVA_MODEL", "anthropic/claude-3.5-sonnet")
else:
    DEFAULT_MODEL = os.environ.get("AGENTNOVA_MODEL", "qwen2.5:0.5b")


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════
MAX_STEPS = int(os.environ.get("AGENTNOVA_MAX_STEPS", "25"))
DEBUG = os.environ.get("AGENTNOVA_DEBUG", "").lower() in ("1", "true", "yes")
VERBOSE = os.environ.get("AGENTNOVA_VERBOSE", "").lower() in ("1", "true", "yes")

# Context window size (Ollama default is 2048)
# Set OLLAMA_NUM_CTX or AGENTNOVA_NUM_CTX to override
NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX") or os.environ.get("AGENTNOVA_NUM_CTX") or "0")
# 0 means use Ollama's default (2048)


# ═══════════════════════════════════════════════════════════════════════════════
# ERROR RETRY SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════
# Whether to automatically retry failed tool calls (default: enabled)
RETRY_ON_ERROR = os.environ.get("AGENTNOVA_RETRY_ON_ERROR", "true").lower() in ("1", "true", "yes")

# Maximum retries per tool call failure (default: 2)
MAX_TOOL_RETRIES = int(os.environ.get("AGENTNOVA_MAX_TOOL_RETRIES") or "2")


@dataclass
class Config:
    """AgentKthx configuration."""
    # Backend URLs
    ollama_base_url: str = field(default_factory=lambda: OLLAMA_BASE_URL)
    llama_server_base_url: str = field(default_factory=lambda: LLAMA_SERVER_BASE_URL)

    # Plugin-owned URLs (read from env vars, defaults from plugin.json)
    bitnet_base_url: str = field(default_factory=lambda: BITNET_BASE_URL)
    zai_base_url: str = field(default_factory=lambda: ZAI_BASE_URL)
    acp_base_url: str = field(default_factory=lambda: ACP_BASE_URL)

    # ACP Credentials
    acp_user: str = field(default_factory=lambda: ACP_USER)
    acp_pass: str = field(default_factory=lambda: ACP_PASS)

    # Backend selection
    backend: str = field(default_factory=lambda: AGENTNOVA_BACKEND)

    # Default model
    default_model: str = field(default_factory=lambda: DEFAULT_MODEL)

    # Agent settings
    max_steps: int = field(default_factory=lambda: MAX_STEPS)
    temperature: float = 0.1
    max_tokens: int = 8192
    num_ctx: int | None = field(default_factory=lambda: _get_num_ctx())

    # Memory settings
    memory_max_messages: int = 50
    memory_max_tokens: int = 4096

    # Security settings
    allow_shell: bool = True
    allow_network: bool = True
    allowed_paths: list[str] = field(default_factory=lambda: ["./output", "./data", "/tmp"])

    # Error retry
    retry_on_error: bool = field(default_factory=lambda: RETRY_ON_ERROR)
    max_tool_retries: int = field(default_factory=lambda: MAX_TOOL_RETRIES)

    # Debug
    debug: bool = field(default_factory=lambda: DEBUG)
    verbose: bool = field(default_factory=lambda: VERBOSE)

    @property
    def ollama_host(self) -> str:
        """Extract host from Ollama URL."""
        parsed = urlparse(self.ollama_base_url)
        return parsed.hostname or "localhost"

    @property
    def ollama_port(self) -> int:
        """Extract port from Ollama URL."""
        parsed = urlparse(self.ollama_base_url)
        return parsed.port or 11434

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls()

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """Load configuration from a JSON file."""
        import json

        try:
            with open(path, "r") as f:
                data = json.load(f)
            return cls(**data)
        except FileNotFoundError:
            return cls()
        except Exception as e:
            print(f"Warning: Error loading config file: {e}")
            return cls()


def _get_num_ctx() -> int | None:
    """Get num_ctx from environment (reads fresh each time)."""
    val = os.environ.get("OLLAMA_NUM_CTX") or os.environ.get("AGENTNOVA_NUM_CTX") or "0"
    num = int(val) if val else 0
    return num if num > 0 else None


# Global config instance
_config: Config | None = None


def get_config(reload: bool = False) -> Config:
    """Get the global configuration.
    
    Args:
        reload: If True, re-read from environment variables
    """
    global _config
    if _config is None or reload:
        _config = Config.from_env()
    return _config


def set_config(config: Config) -> None:
    """Set the global configuration."""
    global _config
    _config = config
