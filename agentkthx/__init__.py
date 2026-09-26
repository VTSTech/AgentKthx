"""
⚛️ AgentKthx R07.04
A minimal, hackable agentic framework engineered for local inference.

Features:
  • Zero dependencies — uses Python stdlib only
  • Ollama + OpenRouter + BitNet backends — switch with --backend flag
  • Three-tier tool support — native, ReAct, or none (auto-detected)
  • Small model optimized — fuzzy matching, argument normalization
  • Built-in security — path validation, command blocklist, SSRF protection
  • Soul Spec v0.5 — persona packages (disabled by default, use --soul)
  • Multi-cloud support — 500+ models via OpenRouter plugin
  • Plugin Spec v0.2 — lifecycle hooks, plugin tools, external plugin roots
  • Update check — stable (PyPI) + development (GitHub commits) release notices

Status: Alpha

Written by VTSTech — https://www.vts-tech.org

Example Usage:
    from agentkthx import Agent
    from agentkthx.tools import make_builtin_registry

    tools = make_builtin_registry().subset(["calculator", "shell"])
    agent = Agent(model="qwen2.5:0.5b", tools=tools)

    result = agent.run("What is 15 * 8?")
    print(result.final_answer)
    
    # With Soul Spec (disabled by default)
    agent = Agent(model="qwen2.5:0.5b", soul="/path/to/soul/package")
"""

__version__ = "0.7.04"  # R07.04
__author__ = "VTSTech"
__status__ = "Alpha"


def _get_git_short_hash() -> str:
    """
    Resolve the source commit for the version suffix, most-accurate first.

    1. Live verified git checkout (``pip install -e .`` / running from a
       clone). A discovered ``.git`` is only trusted when its
       ``remote.origin.url`` points at VTSTech/AgentKthx — otherwise a
       pip-installed package that merely sits inside the *user's* repo would
       report that repo's hash. Dirty trees are marked: ``acf1d72-dirty``.
    2. ``agentkthx._git_meta.SOURCE_COMMIT`` — the commit baked in at build
       time by setup.py (covers PyPI wheels and ``pip install git+https://…``
       where pip's temporary clone is discarded after the wheel is built).
    3. ``""`` — plain version, no suffix.
    """
    import os
    import subprocess

    def _run(cmd: list) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5)

    # --- 1. live verified checkout -------------------------------------
    try:
        check_dir = os.path.dirname(os.path.abspath(__file__))
        for _ in range(3):  # package dir + 2 parents — never farther
            git_path = os.path.join(check_dir, ".git")
            if os.path.exists(git_path):  # dir, or a file (worktree/submodule)
                # Attribution guard: only trust OUR repository.
                origin = _run(
                    ["git", "-C", check_dir, "remote", "get-url", "origin"]
                )
                url = origin.stdout.strip().lower() if origin.returncode == 0 else ""
                if "vtstech/agentkthx" in url:
                    desc = _run(
                        ["git", "-C", check_dir, "describe", "--always",
                         "--dirty", "--abbrev=7"]
                    )
                    if desc.returncode == 0 and desc.stdout.strip():
                        return desc.stdout.strip()
                # .git exists but it is not our repo (or describe failed):
                # stop — do not keep walking into random parent repos.
                break
            parent = os.path.dirname(check_dir)
            if parent == check_dir:
                break
            check_dir = parent
    except Exception:
        pass

    # --- 2. commit baked at build time (PyPI / git+https wheels) --------
    try:
        from . import _git_meta
        if _git_meta.SOURCE_COMMIT:
            return _git_meta.SOURCE_COMMIT
    except ImportError:
        pass

    return ""


# Append git commit hash to version if available
_git_hash = _get_git_short_hash()
if _git_hash:
    __version__ = f"{__version__}-{_git_hash}"

from .agent import Agent
from .agent_mode import AgentMode, AgentState, TaskPlan
from .orchestrator import Orchestrator, AgentCard
from .core.models import StepResult, AgentRun, Tool, ToolParam
from .core.types import StepResultType, ToolSupportLevel, BackendType
from .tools import ToolRegistry, make_builtin_registry, BUILTIN_REGISTRY
from .backends import (
    BaseBackend, OllamaBackend, LlamaServerBackend,
    get_default_backend, get_backend, get_backend_choices,
)
from .config import Config, get_config
from .config import (
    OLLAMA_BASE_URL,
    BITNET_BASE_URL,
    ZAI_BASE_URL,
    OPENROUTER_BASE_URL,
    OPENROUTER_API_KEY,
    OPENROUTER_DEFAULT_MODEL,
    ACP_BASE_URL,
    ACP_USER,
    ACP_PASS,
    DEFAULT_MODEL,
    AGENTKTHX_BACKEND,
)
from .model_discovery import (
    get_models, get_available_models, pick_best_model,
    pick_models_for_benchmark, model_exists, get_client,
)
from .shared_args import SharedConfig, add_shared_args, parse_shared_args

# Persistent memory (graceful import for minimal installs)
try:
    from .core.persistent_memory import PersistentMemory
except ImportError:
    PersistentMemory = None  # type: ignore

# Optional ACP plugin (graceful import — now via plugin system)
try:
    from .plugins.acp.acp_plugin import ACPPlugin
except ImportError:
    ACPPlugin = None  # type: ignore

# Optional Soul Spec support (graceful import)
try:
    from .soul import (
        SoulManifest, SoulLoader, load_soul, build_system_prompt,
        Environment, InteractionMode, HardwareConstraints,
    )
except ImportError:
    SoulManifest = None  # type: ignore
    SoulLoader = None  # type: ignore
    load_soul = None  # type: ignore
    build_system_prompt = None  # type: ignore
    Environment = None  # type: ignore
    InteractionMode = None  # type: ignore
    HardwareConstraints = None  # type: ignore

__all__ = [
    # Version
    "__version__",
    "__author__",
    "__status__",
    # Agent
    "Agent",
    "AgentMode",
    "AgentState",
    "TaskPlan",
    # Memory
    "PersistentMemory",
    # Orchestrator
    "Orchestrator",
    "AgentCard",
    # Models
    "StepResult",
    "AgentRun",
    "Tool",
    "ToolParam",
    # Types
    "StepResultType",
    "ToolSupportLevel",
    "BackendType",
    # Tools
    "ToolRegistry",
    "make_builtin_registry",
    "BUILTIN_REGISTRY",
    # Backends
    "BaseBackend",
    "OllamaBackend",
    "LlamaServerBackend",
    "get_default_backend",
    "get_backend",
    "get_backend_choices",
    # Config
    "Config",
    "get_config",
    "OLLAMA_BASE_URL",
    "BITNET_BASE_URL",
    "ZAI_BASE_URL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_DEFAULT_MODEL",
    "ACP_BASE_URL",
    "ACP_USER",
    "ACP_PASS",
    "DEFAULT_MODEL",
    "AGENTKTHX_BACKEND",
    # Model Discovery
    "get_models",
    "get_available_models",
    "pick_best_model",
    "pick_models_for_benchmark",
    "model_exists",
    "get_client",
    # Shared Args
    "SharedConfig",
    "add_shared_args",
    "parse_shared_args",
    # ACP Plugin
    "ACPPlugin",
    # Soul Spec
    "SoulManifest",
    "SoulLoader",
    "load_soul",
    "build_system_prompt",
    "Environment",
    "InteractionMode",
    "HardwareConstraints",
]