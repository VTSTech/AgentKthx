"""
AgentKthx - TurboQuant Server Manager
Manages llama.cpp TurboQuant server lifecycle: start, stop, status.

Uses Ollama blobs directly as GGUF model files (no conversion needed).
Server binary must be compiled separately from:
  https://github.com/TheTom/llama-cpp-turboquant

Written by VTSTech - https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agentkthx.backends.ollama_registry import (
    OllamaModel,
    discover_models,
    find_model,
    recommended_turbo_config,
    OLLAMA_BLOBS_DIR,
)
from agentkthx.colors import (
    bold, green, red, yellow, cyan, dim, bright_cyan, bright_green,
    bright_yellow, bright_magenta, bright_red, pad_colored,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Default llama-server binary path
TURBOQUANT_SERVER_PATH = os.environ.get(
    "TURBOQUANT_SERVER_PATH",
    "llama-server",
)

# Default port (must match LLAMA_SERVER_BASE_URL in config.py)
TURBOQUANT_DEFAULT_PORT = int(os.environ.get("TURBOQUANT_PORT", "8764"))

# Default context window
TURBOQUANT_DEFAULT_CTX = int(os.environ.get("TURBOQUANT_CTX", "8192"))

# PID file for tracking running server
TURBOQUANT_PID_FILE = Path(os.path.expanduser("~/.agentkthx/turbo.pid"))

# State file for tracking server config
TURBOQUANT_STATE_FILE = Path(os.path.expanduser("~/.agentkthx/turbo.state"))

# Server log file
TURBOQUANT_LOG_FILE = Path(os.path.expanduser("~/.agentkthx/turbo.log"))

# Current state file schema version
_TURBO_STATE_VERSION = 1

# Valid cache types for TurboQuant
VALID_CACHE_TYPES = ("q8_0", "q4_0", "turbo2", "turbo3", "turbo4", "f16")


# ═══════════════════════════════════════════════════════════════════════════════
# SERVER STATE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TurboState:
    """Tracks the running TurboQuant server state."""
    _version: int = 1
    pid: int = 0
    model_name: str = ""
    blob_path: str = ""
    port: int = 0
    ctx: int = 0
    cache_type_k: str = ""
    cache_type_v: str = ""
    turbo_mode: str = ""
    host: str = "localhost"
    server_path: str = ""
    flash_attn: bool = False
    sparsity: float = 0.0
    started_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "_version": _TURBO_STATE_VERSION,
            "pid": self.pid,
            "model_name": self.model_name,
            "blob_path": self.blob_path,
            "port": self.port,
            "ctx": self.ctx,
            "cache_type_k": self.cache_type_k,
            "cache_type_v": self.cache_type_v,
            "turbo_mode": self.turbo_mode,
            "host": self.host,
            "server_path": self.server_path,
            "flash_attn": self.flash_attn,
            "sparsity": self.sparsity,
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TurboState":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def save(self) -> None:
        """Save state to file."""
        TURBOQUANT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        TURBOQUANT_STATE_FILE.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls) -> Optional["TurboState"]:
        """Load state from file, handling schema versioning."""
        if not TURBOQUANT_STATE_FILE.exists():
            return None
        try:
            data = json.loads(TURBOQUANT_STATE_FILE.read_text())
            version = data.pop("_version", 0)
            if version > _TURBO_STATE_VERSION:
                # State file from a newer version of AgentKthx — cannot load
                return None
            # Future: add migration logic here for version < _TURBO_STATE_VERSION
            return cls.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return None

    @classmethod
    def clear(cls) -> None:
        """Clear saved state."""
        if TURBOQUANT_STATE_FILE.exists():
            TURBOQUANT_STATE_FILE.unlink()
        if TURBOQUANT_PID_FILE.exists():
            TURBOQUANT_PID_FILE.unlink()


# ═══════════════════════════════════════════════════════════════════════════════
# SERVER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def _is_process_alive(pid: int) -> bool:
    """Check if a process is running AND not a zombie.

    R06.57: A zombie process (state 'Z') still responds to ``os.kill(pid, 0)``
    but is effectively dead — it won't do anything and may still hold a port
    socket in TIME_WAIT. Read ``/proc/<pid>/stat`` to check the process state
    and treat zombies as dead.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)  # Signal 0 = check existence
    except (ProcessLookupError, PermissionError, OSError):
        return False
    # Check if it's a zombie (state 'Z') via /proc on Linux
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            stat = f.read().split()
            # stat[2] is the process state: 'R' running, 'S' sleeping, 'Z' zombie, etc.
            state = stat[2] if len(stat) > 2 else "?"
            if state == "Z":
                return False  # Zombie — treat as dead
    except (FileNotFoundError, IndexError, PermissionError, OSError):
        pass  # Not Linux or can't read /proc — assume alive (os.kill said so)
    return True


def _free_port(port: int, host: str = "localhost") -> bool:
    """Kill any process listening on the given port.

    R06.57: Prevents "couldn't bind HTTP server socket" errors when a
    zombie or leftover llama-server process is still holding the port.
    Uses ``fuser`` on Linux (Colab) to find and kill the process.

    Returns:
        True if a process was killed, False if the port was already free.
    """
    import subprocess as _sp
    try:
        result = _sp.run(
            ["fuser", f"{port}/tcp"],
            capture_output=True, text=True, timeout=3.0
        )
        if result.returncode == 0 and result.stdout.strip():
            # Port is in use — kill the processes
            pids = result.stdout.strip().split()
            for pid_str in pids:
                try:
                    pid = int(pid_str.strip())
                    os.kill(pid, signal.SIGKILL)
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print(f"  [TurboQuant] Killed stale process {pid} on port {port}")
                except (ValueError, ProcessLookupError, PermissionError, OSError):
                    pass
            time.sleep(0.5)  # Give the OS time to release the socket
            return True
    except (FileNotFoundError, _sp.TimeoutExpired, OSError):
        pass  # fuser not available — skip cleanup
    return False


def _get_running_state() -> Optional[TurboState]:
    """Get state of currently running server, or None if not running."""
    state = TurboState.load()
    if state is None or state.pid == 0:
        return None
    if not _is_process_alive(state.pid):
        # Stale state - process died
        TurboState.clear()
        return None
    return state


def _check_server_health(host: str, port: int, timeout: float = 3.0) -> bool:
    """Check if the TurboQuant server HTTP endpoint is responding."""
    try:
        from urllib.request import urlopen, Request
        from urllib.error import URLError
        url = f"http://{host}:{port}/health"
        req = Request(url, method="GET")
        urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def _build_command(
    server_path: str,
    model_path: str,
    port: int,
    ctx: int,
    cache_type_k: str,
    cache_type_v: str,
    flash_attn: bool = False,
    sparsity: float = 0.0,
    num_threads: int = 0,
    extra_args: Optional[list[str]] = None,
) -> list[str]:
    """Build the llama-server command line."""
    cmd = [
        server_path,
        "-m", model_path,
        "-c", str(ctx),
        "--port", str(port),
        "--host", "0.0.0.0",
    ]

    # TurboQuant KV cache types
    if cache_type_k:
        cmd.extend(["-ctk", cache_type_k])
    if cache_type_v:
        cmd.extend(["-ctv", cache_type_v])

    # Flash attention
    if flash_attn:
        cmd.append("-fa")

    # Sparse V decoding (attention-gated skip)
    if sparsity > 0.0:
        cmd.extend(["--flash-attn-sparsity", str(sparsity)])

    # CPU threads (0 = auto-detect)
    if num_threads > 0:
        cmd.extend(["-t", str(num_threads)])

    # Extra args passthrough
    if extra_args:
        cmd.extend(extra_args)

    return cmd


def start_server(
    model_name: str,
    server_path: Optional[str] = None,
    port: Optional[int] = None,
    ctx: Optional[int] = None,
    cache_type_k: Optional[str] = None,
    cache_type_v: Optional[str] = None,
    flash_attn: bool = False,
    sparsity: float = 0.0,
    num_threads: int = 0,
    wait_ready: bool = True,
    ready_timeout: int = 120,
    extra_args: Optional[list[str]] = None,
    ollama_dir: Optional[Path] = None,
) -> TurboState:
    """Start a TurboQuant llama-server with an Ollama model.

    Args:
        model_name: Ollama model name (e.g. "qwen2.5:7b") or path to a GGUF file
        server_path: Path to llama-server binary (default: TURBOQUANT_SERVER_PATH)
        port: Server port (default: TURBOQUANT_DEFAULT_PORT)
        ctx: Context window size (default: TURBOQUANT_DEFAULT_CTX)
        cache_type_k: K cache type (default: auto-detected from weight quant)
        cache_type_v: V cache type (default: auto-detected from weight quant)
        flash_attn: Enable flash attention
        sparsity: Sparse V decoding sparsity threshold (0.0 = disabled)
        num_threads: CPU thread count (0 = auto-detect)
        wait_ready: Wait for server to be ready before returning
        ready_timeout: Max seconds to wait for server readiness
        extra_args: Additional arguments to pass to llama-server
        ollama_dir: Override Ollama models directory

    Returns:
        TurboState with server information

    Raises:
        FileNotFoundError: If model not found or server binary not found
        RuntimeError: If server fails to start or become ready
    """
    # Check if already running
    existing = _get_running_state()
    if existing is not None:
        raise RuntimeError(
            f"TurboQuant server already running (PID {existing.pid}, "
            f"model: {existing.model_name}, port: {existing.port}). "
            f"Run 'agentkthx turbo stop' first."
        )

    # Resolve server path
    server_path = server_path or TURBOQUANT_SERVER_PATH
    if not Path(server_path).exists() and not _find_in_path(server_path):
        raise FileNotFoundError(
            f"llama-server binary not found: {server_path}\n"
            f"Set TURBOQUANT_SERVER_PATH env var or compile from:\n"
            f"  https://github.com/TheTom/llama-cpp-turboquant"
        )

    # Resolve model path
    model_path: str
    ollama_model: Optional[OllamaModel] = None

    if os.path.isfile(model_name) and model_name.endswith(".gguf"):
        # Direct GGUF file path
        model_path = os.path.abspath(model_name)
        weight_quant = "UNKNOWN"
    else:
        # Ollama model name - look up in registry
        ollama_model = find_model(model_name, ollama_dir=ollama_dir)
        if ollama_model is None:
            # Try as-is (might be a direct path)
            if not os.path.isfile(model_name):
                raise FileNotFoundError(
                    f"Model '{model_name}' not found in Ollama registry.\n"
                    f"Run 'agentkthx turbo list' to see available models.\n"
                    f"Or provide a direct GGUF file path."
                )
            model_path = os.path.abspath(model_name)
            weight_quant = "UNKNOWN"
        elif not ollama_model.exists:
            raise FileNotFoundError(
                f"Model '{model_name}' found but blob missing:\n"
                f"  {ollama_model.blob_path}\n"
                f"The model may have been removed from Ollama. "
                f"Re-pull with: ollama pull {model_name}"
            )
        else:
            model_path = str(ollama_model.blob_path)
            weight_quant = ollama_model.weight_quant

    # R06.57: Removed the head_dim >= 128 compatibility check.
    # Empirical testing on Colab (2026-09-25) showed the check was wrong:
    # 8/10 models with head_dim=64 loaded fine with turbo4/turbo4, and
    # functiongemma:270m (head_dim=128) actually FAILED. The real determinant
    # is whether the TurboQuant fork's ggml supports the model's architecture
    # (e.g. qwen35.rope.dimension_sections is unsupported), not head_dim.
    # The server will report a clear error if a model can't load — no need
    # to pre-block based on a wrong heuristic.

    # Auto-detect TurboQuant config if not specified
    if cache_type_k is None or cache_type_v is None:
        config = recommended_turbo_config(weight_quant)
        if cache_type_k is None:
            cache_type_k = config["cache_type_k"]
        if cache_type_v is None:
            cache_type_v = config["cache_type_v"]
        turbo_mode = config["mode"]
        auto_reason = config["reason"]
    else:
        turbo_mode = "custom"

    # Validate cache types
    for ct_name, ct_val in [("K", cache_type_k), ("V", cache_type_v)]:
        if ct_val.lower() not in VALID_CACHE_TYPES:
            raise ValueError(
                f"Invalid cache type for {ct_name}: '{ct_val}'. "
                f"Valid types: {', '.join(VALID_CACHE_TYPES)}"
            )

    # Apply defaults
    port = port or TURBOQUANT_DEFAULT_PORT
    ctx = ctx or TURBOQUANT_DEFAULT_CTX
    host = "localhost"

    # R06.57: Free the port before starting — kill any stale/zombie
    # llama-server process that's still holding the port socket.
    # This is what the Colab notebook cell does with `!pkill llama-server`.
    if _free_port(port, host):
        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [TurboQuant] Freed port {port} from stale process")

    # Build command
    cmd = _build_command(
        server_path=server_path,
        model_path=model_path,
        port=port,
        ctx=ctx,
        cache_type_k=cache_type_k,
        cache_type_v=cache_type_v,
        flash_attn=flash_attn,
        sparsity=sparsity,
        num_threads=num_threads,
        extra_args=extra_args,
    )

    # Print startup info
    print(bold(bright_cyan("TURBOQUANT")) + dim(" — starting llama-server"))
    print(f"  Model:       {bright_green(model_name)}")
    print(f"  Blob:        {dim(model_path)}")
    print(f"  Weights:     {yellow(weight_quant)}")
    print(f"  KV Cache:    {bright_magenta(f'{cache_type_k}/{cache_type_v}')} ({turbo_mode})")
    if turbo_mode != "custom":
        print(f"  Auto-config: {dim(auto_reason)}")
    print(f"  Context:     {ctx} tokens")
    print(f"  Port:        {port}")
    print(f"  Flash Attn:  {'ON' if flash_attn else dim('off')}")
    if sparsity > 0:
        print(f"  Sparse V:    {sparsity}")
    print(f"  Server:      {server_path}")
    print()

    # Start the server
    log_file = open(TURBOQUANT_LOG_FILE, "a")
    process = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=log_file,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # Detach from parent process group
    )

    pid = process.pid

    # Save state
    state = TurboState(
        pid=pid,
        model_name=model_name,
        blob_path=model_path,
        port=port,
        ctx=ctx,
        cache_type_k=cache_type_k,
        cache_type_v=cache_type_v,
        turbo_mode=turbo_mode,
        host=host,
        server_path=server_path,
        flash_attn=flash_attn,
        sparsity=sparsity,
        started_at=time.time(),
    )
    state.save()

    # Save PID file
    TURBOQUANT_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    TURBOQUANT_PID_FILE.write_text(str(pid))

    # Wait for server to be ready
    if wait_ready:
        print(f"  Starting... ", end="", flush=True)
        start_wait = time.time()
        ready = False
        while time.time() - start_wait < ready_timeout:
            if not _is_process_alive(pid):
                # Process died — read the last few log lines to show why
                state_out = ""
                if TURBOQUANT_STATE_FILE.exists():
                    TURBOQUANT_STATE_FILE.unlink()
                if TURBOQUANT_PID_FILE.exists():
                    TURBOQUANT_PID_FILE.unlink()
                print(bright_red("FAILED"))
                # Read last 5 lines of the log for the error message
                log_tail = ""
                try:
                    log_file.close()
                    with open(TURBOQUANT_LOG_FILE, "r") as lf:
                        lines = lf.readlines()
                        log_tail = "".join(lines[-5:]) if lines else "(empty log)"
                except Exception:
                    log_tail = "(could not read log)"
                raise RuntimeError(
                    f"llama-server process (PID {pid}) died during startup.\n"
                    f"Check that the model file is valid and the server binary is compiled correctly.\n"
                    f"Last log lines:\n{log_tail}"
                )
            if _check_server_health(host, port, timeout=2.0):
                ready = True
                break
            time.sleep(0.5)
            print(".", end="", flush=True)

        if ready:
            elapsed = time.time() - start_wait
            print(bright_green("READY") + dim(f" ({elapsed:.1f}s)"))
        else:
            print(bright_yellow("TIMEOUT"))
            print(yellow(f"  Warning: Server may still be starting. PID: {pid}"))
            print(yellow(f"  Check: curl http://{host}:{port}/health"))

    print()
    return state


def stop_server(force: bool = False) -> bool:
    """Stop the running TurboQuant server.

    Args:
        force: Force-kill with SIGKILL instead of SIGTERM

    Returns:
        True if server was stopped, False if no server was running
    """
    state = _get_running_state()
    if state is None:
        # Check for stale PID file
        if TURBOQUANT_PID_FILE.exists():
            TURBOQUANT_PID_FILE.unlink()
        if TURBOQUANT_STATE_FILE.exists():
            TURBOQUANT_STATE_FILE.unlink()
        return False

    pid = state.pid
    model_name = state.model_name
    uptime = time.time() - state.started_at if state.started_at else 0

    try:
        if force:
            os.kill(pid, signal.SIGKILL)
            sig_name = "SIGKILL"
        else:
            os.kill(pid, signal.SIGTERM)
            sig_name = "SIGTERM"

        # Wait for process to die (up to 10s)
        for _ in range(20):
            if not _is_process_alive(pid):
                break
            time.sleep(0.5)
        else:
            # Still alive after 10s, force kill
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    except ProcessLookupError:
        pass  # Already dead
    except PermissionError:
        print(bright_red(f"  Permission denied killing PID {pid}. Try --force."))

    # Cleanup
    TurboState.clear()

    # Format uptime
    if uptime < 60:
        uptime_str = f"{uptime:.0f}s"
    elif uptime < 3600:
        uptime_str = f"{uptime / 60:.1f}m"
    else:
        uptime_str = f"{uptime / 3600:.1f}h"

    print(bold(bright_cyan("TURBOQUANT")) + dim(" — server stopped"))
    print(f"  Model:   {bright_green(model_name)}")
    print(f"  PID:     {pid} ({sig_name})")
    print(f"  Uptime:  {uptime_str}")
    print()

    return True


def get_status() -> Optional[TurboState]:
    """Get status of the running TurboQuant server.

    Returns:
        TurboState if running, None otherwise
    """
    state = _get_running_state()
    return state


def _find_in_path(name: str) -> bool:
    """Check if a binary exists in PATH."""
    from shutil import which
    return which(name) is not None


# ═══════════════════════════════════════════════════════════════════════════════
# FORMATTED OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

def print_model_list(models: list[OllamaModel], source: str = "local", backend_url: str | None = None) -> None:
    """Print a formatted table of discovered Ollama models.

    Args:
        models: List of OllamaModel objects
        source: "api" if models discovered via Ollama API, "local" if filesystem
        backend_url: Ollama server URL (shown when source="api")
    """
    if not models:
        print(yellow("No Ollama models found."))
        if source == "api":
            print(dim("  No models on the Ollama server."))
        else:
            print(dim("  Check that ~/.ollama/models/ exists and contains manifests."))
            print(dim("  Pull models with: ollama pull <model_name>"))
        return

    print(bold(bright_cyan("TURBOQUANT")) + dim(" — available Ollama models"))
    if source == "api" and backend_url:
        print(f"  {dim(f'Backend: {backend_url}')}")
    else:
        print(f"  {dim(f'Models dir: {OLLAMA_BLOBS_DIR.parent}')}")
    print()

    # Table header
    name_w = max(len(m.name) for m in models) + 2
    name_w = max(name_w, 10)
    size_w = 10
    quant_w = 12
    turbo_w = 28

    header = (
        pad_colored(bold("Model"), name_w) +
        pad_colored(bold("Size"), size_w, "right") +
        "  " +
        pad_colored(bold("Weights"), quant_w) +
        "  " +
        pad_colored(bold("TurboQuant Config"), turbo_w)
    )
    print(header)
    print(dim("  " + "-" * (name_w + size_w + quant_w + turbo_w + 6)))

    for model in models:
        exists_marker = bright_green("●") if model.exists else bright_red("✗")
        name_str = f"{exists_marker} {model.name}"
        size_str = model.size_human
        quant_str = model.weight_quant
        is_not_pulled = quant_str == "not pulled"

        # R06.57: Show recommended turbo config for all models — the
        # head_dim >= 128 check was wrong (empirical testing showed
        # head_dim=64 models work fine). Just show the recommended config
        # and let the user try it.
        if not is_not_pulled:
            config = recommended_turbo_config(model.weight_quant)
            turbo_str = f"{config['cache_type_k']}/{config['cache_type_v']}"
            mode_str = dim(f"({config['mode']})")
            compat_color = bright_green
        else:
            turbo_str = dim("N/A")
            mode_str = dim("(not pulled)")
            compat_color = dim

        line = (
            pad_colored(name_str, name_w) +
            pad_colored(size_str, size_w, "right") +
            "  " +
            pad_colored(dim(quant_str) if is_not_pulled else cyan(quant_str), quant_w) +
            "  " +
            pad_colored(compat_color(turbo_str), quant_w + 2) + mode_str
        )
        print(line)

    # Count models (R06.57: no more head_dim compatibility gate)
    n_pulled = sum(1 for m in models if m.weight_quant != "not pulled")
    n_not_pulled = sum(1 for m in models if m.weight_quant == "not pulled")

    print()
    print(dim(f"  {len(models)} model(s) found"))
    if n_pulled:
        print(bright_green(f"  {n_pulled} available for TurboQuant"))
    if n_not_pulled:
        print(dim(f"  {n_not_pulled} not pulled locally (pull with: ollama pull <name>)"))
    if source == "api":
        print(dim("  ● = blob exists  ✗ = blob missing / not pulled"))
    else:
        print(dim("  ● = blob exists  ✗ = blob missing"))
    print()


def print_status(state: TurboState) -> None:
    """Print formatted server status."""
    uptime = time.time() - state.started_at if state.started_at else 0
    if uptime < 60:
        uptime_str = f"{uptime:.0f}s"
    elif uptime < 3600:
        uptime_str = f"{uptime / 60:.1f}m"
    else:
        uptime_str = f"{uptime / 3600:.1f}h"

    healthy = _check_server_health(state.host, state.port)
    health_str = bright_green("HEALTHY") if healthy else bright_red("UNREACHABLE")

    print(bold(bright_cyan("TURBOQUANT")) + dim(" — server status"))
    print(f"  Status:     {health_str}")
    print(f"  Model:      {bright_green(state.model_name)}")
    print(f"  PID:        {state.pid}")
    print(f"  Port:       {state.port}")
    print(f"  Endpoint:   {cyan(f'http://{state.host}:{state.port}/v1/chat/completions')}")
    print(f"  KV Cache:   {bright_magenta(f'{state.cache_type_k}/{state.cache_type_v}')} ({state.turbo_mode})")
    print(f"  Context:    {state.ctx} tokens")
    print(f"  Uptime:     {uptime_str}")
    print(f"  Server:     {dim(state.server_path)}")
    if state.flash_attn:
        print(f"  Flash Attn: {bright_green('ON')}")
    if state.sparsity > 0:
        print(f"  Sparse V:   {state.sparsity}")
    print()

    # Show how to use with AgentKthx
    print(dim("  Usage with AgentKthx:"))
    usage1 = f'agentkthx run --backend llama-server --model {state.model_name} "<prompt>"'
    usage2 = f'OLLAMA_BASE_URL=http://{state.host}:{state.port} agentkthx run "<prompt>"'
    print(f"    {cyan(usage1)}")
    print(f"    {cyan(usage2)}")
    print()


__all__ = [
    "TurboState",
    "TURBOQUANT_SERVER_PATH",
    "TURBOQUANT_DEFAULT_PORT",
    "TURBOQUANT_DEFAULT_CTX",
    "TURBOQUANT_PID_FILE",
    "TURBOQUANT_STATE_FILE",
    "VALID_CACHE_TYPES",
    "start_server",
    "stop_server",
    "get_status",
    "print_model_list",
    "print_status",
]