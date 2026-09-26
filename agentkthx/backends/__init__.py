"""
⚛️ AgentKthx — Backends Module
Backend implementations for different inference engines.

Native backends (ollama, llama-server) ship here.  All other backends
(bitnet, zai, ...) are registered dynamically via the plugin system in
``agentkthx/plugins/`` and are discovered by ``PluginManager``.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

from .base import BaseBackend
from .ollama import OllamaBackend
from .llama_server import LlamaServerBackend
# MAINT-02 (R07.05): expose CloudBackend so cloud plugin authors can
# inherit from it instead of duplicating the cloud-backend boilerplate.
from .cloud_base import CloudBackend
from ..config import AGENTKTHX_BACKEND, OLLAMA_BASE_URL, LLAMA_SERVER_BASE_URL
from ..core.types import ApiMode


# ---------------------------------------------------------------------------
# Native backend registry (always available, no plugin overhead)
# ---------------------------------------------------------------------------
_BACKENDS: dict[str, type[BaseBackend]] = {
    "ollama": OllamaBackend,
    "llama-server": LlamaServerBackend,
    "llama_server": LlamaServerBackend,  # alias
}


def _ensure_plugin(name: str) -> None:
    """
    Lazy-load the plugin that provides a backend.

    Called when a backend name is requested but not in the native registry.
    Resolves the backend name to a plugin name via PluginManager's manifest
    scan (e.g. ``"test-backend"`` → ``"test-plugin"``).
    """
    from ..plugins import get_plugin_manager
    pm = get_plugin_manager()
    if pm.is_loaded(name):
        return  # already loaded

    # Backend name might differ from plugin name — resolve it
    plugin_name = pm.find_plugin_for_backend(name)
    if plugin_name is None:
        # Fall back: maybe the backend name IS the plugin name (e.g. "bitnet")
        plugin_name = name

    plugin = pm.load(plugin_name)
    if plugin is None:
        raise ValueError(
            f"Cannot load backend '{name}'. "
            f"Plugin '{plugin_name}' not found or failed to load."
        )
    # Plugin should have registered its backend via register_backend().
    # Verify it's actually available now.
    if pm.get_backend_class(name) is None:
        raise ValueError(
            f"Plugin '{plugin_name}' loaded but did not register a backend class."
        )


def get_backend(name: str, timeout: int | None = None, api_mode: ApiMode | str | None = None, **kwargs) -> BaseBackend:
    """
    Get a backend instance by name.

    Resolves backend classes from the native registry first, then from
    the plugin system via lazy loading.

    Args:
        name: Backend name ("ollama", "bitnet", "zai", "llama-server", ...)
        timeout: Request timeout in seconds (default: 120)
        api_mode: API mode ("openre" for OpenResponses, "openai" for Chat-Completions)
        **kwargs: Backend-specific configuration

    Returns:
        Backend instance
    """
    from .base import BackendConfig

    name_lower = name.lower()

    # Resolve from native registry first
    backend_class: type[BaseBackend] | None = _BACKENDS.get(name_lower)

    # If not native, try the plugin system (lazy load)
    if backend_class is None:
        _ensure_plugin(name_lower)
        from ..plugins import get_plugin_manager
        pm = get_plugin_manager()
        backend_class = pm.get_backend_class(name_lower)
        if backend_class is None:
            raise ValueError(
                f"Unknown backend: '{name}'. "
                f"Available native: {list(_BACKENDS.keys())}"
            )

    # Pass appropriate base_url if not provided
    if "base_url" not in kwargs:
        if name_lower == "ollama":
            kwargs["base_url"] = OLLAMA_BASE_URL
        elif name_lower == "llama-server":
            kwargs["base_url"] = LLAMA_SERVER_BASE_URL
        # Plugin backends set their own defaults in their __init__

    # BitNet-specific: route to llama-server with bitnet_mode=True
    if name_lower == "bitnet" and "bitnet_mode" not in kwargs:
        kwargs["bitnet_mode"] = True

    # Create BackendConfig with timeout if specified
    if "config" not in kwargs and timeout is not None:
        kwargs["config"] = BackendConfig(timeout=timeout)

    # Pass api_mode for backends that support it
    if api_mode is not None and "api_mode" not in kwargs:
        kwargs["api_mode"] = api_mode

    return backend_class(**kwargs)


def get_default_backend(name: str | None = None, api_mode: ApiMode | str | None = None, timeout: int | None = None) -> BaseBackend:
    """
    Get a default backend instance.

    Uses AGENTKTHX_BACKEND env var if name not provided.

    Args:
        name: Backend name (optional, uses env var if not provided)
        api_mode: API mode ("openre" for OpenResponses/native, "openai" for Chat-Completions)
        timeout: Request timeout in seconds (default: 120)

    Returns:
        Backend instance with default configuration
    """
    backend_name = name or AGENTKTHX_BACKEND
    return get_backend(backend_name, api_mode=api_mode, timeout=timeout)


def register_backend(name: str, backend_class: type[BaseBackend]) -> None:
    """
    Register a backend class (used by plugins).

    Args:
        name: Backend name
        backend_class: Backend class
    """
    _BACKENDS[name.lower()] = backend_class


def get_backend_choices() -> list[str]:
    """
    Get the merged list of available backend names for CLI ``--backend``.

    Includes native backends plus any registered by plugins. The
    PluginManager version reads from each plugin's
    ``cli_flags."--backend"`` manifest entry — this means a plugin
    that registers aliases (e.g. huggingface plugin registers both
    ``huggingface`` and ``hf``) will have all of them surfaced as
    valid CLI choices.
    """
    from ..plugins import get_plugin_manager
    pm = get_plugin_manager()
    # R07: delegate to PluginManager.get_backend_choices() so plugin-
    # registered aliases (e.g. "hf" → "huggingface") are surfaced as
    # valid --backend choices. Previously this called pm.list_backend_names()
    # which only returns the canonical names registered via
    # register_backend() — aliases declared in plugin.json's cli_flags
    # were silently dropped.
    native = list(_BACKENDS.keys())
    plugin_values = pm.get_backend_choices()
    # Merge, deduplicate, preserve order
    seen = set()
    result = []
    for n in native + plugin_values:
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


__all__ = [
    "BaseBackend",
    "CloudBackend",
    "OllamaBackend",
    "LlamaServerBackend",
    "get_backend",
    "get_default_backend",
    "register_backend",
    "get_backend_choices",
    "ApiMode",
]
