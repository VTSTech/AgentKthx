"""
⚛️ AgentKthx — Model Discovery

Dynamic model discovery for Ollama and BitNet backends.
Discovers available models at runtime instead of hardcoding.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

from typing import Optional

from .config import AGENTKTHX_BACKEND
from .backends import get_backend, get_default_backend, BaseBackend


def get_client(backend: Optional[str] = None) -> BaseBackend:
    """
    Get a backend client for the specified or default backend.

    Parameters
    ----------
    backend : str, optional
        "ollama" or "bitnet". Defaults to AGENTKTHX_BACKEND from config.

    Returns
    -------
    BaseBackend
        Backend instance (OllamaBackend or BitNetBackend)
    """
    if backend is None:
        return get_default_backend()
    return get_backend(backend)


def get_ollama_models(backend: Optional[BaseBackend] = None) -> list[str]:
    """
    Get list of available models from Ollama.

    Parameters
    ----------
    backend : BaseBackend, optional
        Backend to use. Creates one if not provided.

    Returns
    -------
    list[str]
        List of model names available in Ollama
    """
    if backend is None:
        backend = get_backend("ollama")

    if not backend.is_running():
        return []

    models = backend.list_models() or []
    return [m.get("name", m) if isinstance(m, dict) else m for m in models]


def get_bitnet_models(backend: Optional[BaseBackend] = None) -> list[str]:
    """
    Get list of available models from BitNet backend.

    Parameters
    ----------
    backend : BaseBackend, optional
        Backend to use. Creates one if not provided.

    Returns
    -------
    list[str]
        List of model names available in BitNet
    """
    try:
        if backend is None:
            backend = get_backend("bitnet")

        if not backend.is_running():
            return []

        models = backend.list_models() or []
        return [m.get("name", m) if isinstance(m, dict) else m for m in models]
    except Exception:
        return []


def get_models(backend: Optional[str] = None, client: Optional[BaseBackend] = None) -> list[str]:
    """
    Get list of available models from the specified or default backend.

    This is the backend-agnostic version that respects AGENTKTHX_BACKEND.

    Parameters
    ----------
    backend : str, optional
        "ollama" or "bitnet". Defaults to AGENTKTHX_BACKEND from config.
    client : BaseBackend, optional
        Backend to use. Creates one if not provided.

    Returns
    -------
    list[str]
        List of model names available on the backend.

    Examples
    --------
    >>> # Use default backend from AGENTKTHX_BACKEND env var
    >>> models = get_models()

    >>> # Explicitly use Ollama
    >>> models = get_models(backend="ollama")

    >>> # Explicitly use BitNet
    >>> models = get_models(backend="bitnet")

    >>> # Pass backend instance positionally (auto-detected)
    >>> models = get_models(client=my_backend)
    """
    # Handle case where client is passed as first positional argument
    if backend is not None and not isinstance(backend, str):
        # backend is actually a backend object
        client = backend
        backend = None

    backend_name = (backend or AGENTKTHX_BACKEND).lower()

    if client is None:
        try:
            client = get_backend(backend_name)
        except Exception:
            return []

    if backend_name == "bitnet":
        return get_bitnet_models(client)
    else:
        return get_ollama_models(client)


def pick_best_model(
    preferred: Optional[str] = None,
    fallback_order: Optional[list[str]] = None,
    backend: Optional[BaseBackend] = None,
) -> Optional[str]:
    """
    Pick the best available model.

    Parameters
    ----------
    preferred : str, optional
        Preferred model name. If available and specified, returns this.
    fallback_order : list[str], optional
        Order of preference for fallback models.
        Default: prioritize small, fast models.
    backend : BaseBackend, optional
        Backend to use for discovery.

    Returns
    -------
    str or None
        Name of the best available model, or None if none available.

    Examples
    --------
    >>> model = pick_best_model()
    >>> model = pick_best_model(preferred="llama3.1:8b")
    >>> model = pick_best_model(fallback_order=["qwen2.5-coder:0.5b", "tinyllama"])
    """
    available = get_models(client=backend)

    if not available:
        return None

    # If preferred model is available, use it
    if preferred and preferred in available:
        return preferred

    # Default fallback order: small fast models first
    if fallback_order is None:
        fallback_order = [
            # Best small models for tools
            "qwen2.5-coder:0.5b-instruct-q4_k_m",
            "qwen2.5-coder:0.5b",
            "qwen2.5:0.5b",
            "granite3.1-moe:1b",
            "llama3.2:1b",
            "gemma3:270m",
            # Common larger models
            "llama3.1:8b",
            "llama3.2:3b",
            "qwen2.5:7b",
            "mistral:7b",
            # Fallback to any tiny model
            "tinyllama:latest",
            "smollm:135m",
            "granite4:350m",
        ]

    # Find first available from fallback order
    for model in fallback_order:
        # Try exact match first
        if model in available:
            return model
        # Try partial match (e.g., "qwen2.5-coder" matches "qwen2.5-coder:0.5b")
        for avail in available:
            if avail.startswith(model.split(":")[0]):
                return avail

    # Last resort: return first available model
    if available:
        return available[0]

    return None


def pick_models_for_benchmark(
    max_models: int = 6,
    prefer_small: bool = True,
    backend: Optional[BaseBackend] = None,
) -> list[str]:
    """
    Pick a set of models suitable for benchmarking.

    Parameters
    ----------
    max_models : int
        Maximum number of models to return
    prefer_small : bool
        If True, prefer small models (< 2B params)
    backend : BaseBackend, optional
        Backend to use for discovery

    Returns
    -------
    list[str]
        List of model names for benchmarking
    """
    available = get_models(client=backend)

    if not available:
        return []

    # Categorize models by size
    small_indicators = ["0.5b", "270m", "135m", "350m", "0.6b", "1b", "tiny", "mini", "micro", "small"]
    medium_indicators = ["3b", "7b", "8b"]

    small_models = []
    medium_models = []
    large_models = []

    for model in available:
        model_lower = model.lower()
        if any(ind in model_lower for ind in small_indicators):
            small_models.append(model)
        elif any(ind in model_lower for ind in medium_indicators):
            medium_models.append(model)
        else:
            large_models.append(model)

    # Build result list
    result = []

    if prefer_small:
        # Add small models first (up to half of max)
        small_count = max(1, max_models // 2)
        result.extend(small_models[:small_count])
        # Fill with medium/large
        remaining = max_models - len(result)
        result.extend(medium_models[:remaining])
        remaining = max_models - len(result)
        result.extend(large_models[:remaining])
    else:
        # Mix of all sizes
        result.extend(small_models[:2])
        result.extend(medium_models[:2])
        result.extend(large_models[:max_models - len(result)])

    # If we still don't have enough, add any remaining
    if len(result) < max_models:
        for model in available:
            if model not in result:
                result.append(model)
                if len(result) >= max_models:
                    break

    return result[:max_models]


def model_exists(model: str, backend: Optional[BaseBackend] = None) -> bool:
    """
    Check if a specific model is available.

    Parameters
    ----------
    model : str
        Model name to check
    backend : BaseBackend, optional
        Backend to use

    Returns
    -------
    bool
        True if model is available
    """
    available = get_models(client=backend)

    # Exact match
    if model in available:
        return True

    # Partial match (e.g., "llama3" matches "llama3.1:8b")
    model_base = model.split(":")[0]
    for avail in available:
        if avail.startswith(model_base):
            return True

    return False


# Convenience exports
__all__ = [
    "get_client",
    "get_models",
    "get_available_models",  # Alias for get_models
    "get_ollama_models",
    "get_bitnet_models",
    "pick_best_model",
    "pick_models_for_benchmark",
    "model_exists",
    "match_models",
    "resolve_model",
]

# Alias for convenience
get_available_models = get_models


def match_models(
    pattern: str,
    backend: Optional[BaseBackend] = None,
    exact_first: bool = True,
) -> list[str]:
    """
    Match models by pattern (fuzzy/partial matching).

    Supports:
    - Exact match: "qwen2.5:0.5b" → ["qwen2.5:0.5b"]
    - Prefix match: "qwen" → all models starting with "qwen"
    - Contains match: "gemma" → all models containing "gemma"
    - Tag match: ":0.5b" → all models with :0.5b tag
    - Size filter: "0.5b" → all models with 0.5b in name
    - Single char: "g" → all models containing "g" (granite, gemma, etc.)

    Parameters
    ----------
    pattern : str
        Pattern to match against model names (case-insensitive)
    backend : BaseBackend, optional
        Backend to use for model discovery
    exact_first : bool
        If True, exact matches are returned first (and only exact if found)

    Returns
    -------
    list[str]
        List of matching model names, sorted alphabetically

    Examples
    --------
    >>> match_models("qwen")
    ['qwen2.5:0.5b', 'qwen2.5-coder:0.5b', 'qwen3:0.6b', 'qwen:0.5b']

    >>> match_models("g")
    ['functiongemma:270m', 'gemma3:270m', 'granite4:350m']

    >>> match_models(":0.5b")
    ['qwen2.5-coder:0.5b', 'qwen2.5:0.5b', 'qwen:0.5b']
    """
    available = get_models(client=backend)
    
    if not available:
        return []
    
    if not pattern:
        return sorted(available)
    
    pattern_lower = pattern.lower()
    
    # Try exact match first
    if exact_first:
        exact_matches = [m for m in available if m.lower() == pattern_lower]
        if exact_matches:
            return exact_matches
    
    # Categorize matches by priority
    starts_with = []
    contains = []
    tag_matches = []
    
    for model in available:
        model_lower = model.lower()
        
        # Check if pattern is a tag (starts with ":")
        if pattern.startswith(":"):
            if model_lower.endswith(pattern_lower):
                tag_matches.append(model)
        else:
            # Starts with pattern (before any ":" tag)
            model_base = model_lower.split(":")[0]
            if model_base.startswith(pattern_lower):
                starts_with.append(model)
            elif pattern_lower in model_lower:
                contains.append(model)
    
    # Combine results: starts_with first, then contains, then tag_matches
    if pattern.startswith(":"):
        result = sorted(tag_matches)
    else:
        result = sorted(starts_with) + sorted(contains)
    
    return result


def resolve_model(
    model_spec: str,
    backend: Optional[BaseBackend] = None,
    allow_multiple: bool = False,
) -> str | list[str]:
    """
    Resolve a model specification to actual model name(s).

    This is the main entry point for CLI model resolution.
    Returns a single model name or list if multiple matches.

    Parameters
    ----------
    model_spec : str
        Model name or pattern to resolve
    backend : BaseBackend, optional
        Backend to use for model discovery
    allow_multiple : bool
        If True, return list of all matches; if False, return first match

    Returns
    -------
    str or list[str]
        Resolved model name(s)

    Raises
    ------
    ValueError
        If no models match the pattern

    Examples
    --------
    >>> resolve_model("qwen2.5:0.5b")  # Exact match
    'qwen2.5:0.5b'

    >>> resolve_model("qwen", allow_multiple=True)  # Multiple matches
    ['qwen2.5:0.5b', 'qwen2.5-coder:0.5b', 'qwen3:0.6b', 'qwen:0.5b']

    >>> resolve_model("qwen")  # First match
    'qwen:0.5b'
    """
    matches = match_models(model_spec, backend=backend)
    
    if not matches:
        raise ValueError(f"No models found matching '{model_spec}'")
    
    if allow_multiple:
        return matches
    
    return matches[0]