"""`agentkthx turbo` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse

from ...backends import get_backend
from ...colors import bold, yellow, dim, cyan, bright_red, bright_cyan
from ...config import get_config
from pathlib import Path




def cmd_turbo(args: argparse.Namespace) -> int:
    """TurboQuant server management commands."""
    from ...plugins.turboquant.turbo import (
        start_server, stop_server, get_status,
        print_model_list, print_status,
        TURBOQUANT_SERVER_PATH,
    )
    from ...backends.ollama_registry import discover_models

    turbo_cmd = getattr(args, "turbo_command", None)

    if not turbo_cmd:
        # No subcommand — show status or help
        state = get_status()
        if state:
            print_status(state)
        else:
            print(bold(bright_cyan("TURBOQUANT")) + dim(" — server management"))
            print()
            print(f"  {bold('Usage:')}")
            print(f"    agentkthx turbo list             List Ollama models")
            print(f"    agentkthx turbo start <model>     Start TurboQuant server")
            print(f"    agentkthx turbo stop              Stop TurboQuant server")
            print(f"    agentkthx turbo status            Show server status")
            print()
            print(f"  {dim('No server running.')} Run {cyan('agentkthx turbo list')} to see available models.")
            print()
        return 0

    if turbo_cmd == "list":
        from ...backends.ollama_registry import OllamaModel

        ollama_dir = Path(args.ollama_dir) if args.ollama_dir else None
        only_existing = not getattr(args, "all", False)
        config = get_config()

        # Try API-based discovery first (same source as `agentkthx models`)
        api_models = None
        api_url = None
        backend_name = getattr(args, "backend", None) or config.backend
        if backend_name == "ollama":
            try:
                backend = get_backend("ollama", api_mode="openre")
                api_models = backend.list_models()
                if api_models:
                    api_url = backend.base_url
            except Exception:
                api_models = None

        if api_models is not None:
            # Got models from API — merge with local GGUF metadata
            local_models = discover_models(ollama_dir=ollama_dir, only_existing=False)

            # Build lookup by both short name ("repo:tag") and full name ("library/repo:tag")
            # discover_models uses short names; the API returns full names with library prefix
            local_lookup: dict[str, OllamaModel] = {}
            for m in local_models:
                local_lookup[m.name] = m
                # Derive full name from manifest path: .../registry.ollama.ai/<library>/<repo>/<tag>
                if m.manifest_path != Path("") and m.manifest_path.parent.parent.name != "registry.ollama.ai":
                    library = m.manifest_path.parent.parent.name
                    full_name = f"{library}/{m.name}"
                    local_lookup[full_name] = m

            models: list = []
            for api_m in api_models:
                name = api_m.get("name", "")
                if not name:
                    continue
                if name in local_lookup:
                    models.append(local_lookup[name])
                else:
                    # Not pulled locally — create minimal OllamaModel from API data
                    repo, tag = name, "latest"
                    if ":" in name:
                        repo, tag = name.rsplit(":", 1)
                    models.append(OllamaModel(
                        name=name,
                        repo=repo,
                        tag=tag,
                        blob_path=Path(""),
                        size_bytes=api_m.get("size", 0),
                        weight_quant="not pulled",
                        manifest_path=Path(""),
                        model_digest="",
                    ))
            models.sort(key=lambda m: m.name)
            print_model_list(models, source="api", backend_url=api_url)
        else:
            # API unreachable — fall back to filesystem discovery
            models = discover_models(ollama_dir=ollama_dir, only_existing=only_existing)
            print_model_list(models, source="local")
        return 0

    elif turbo_cmd == "start":
        try:
            state = start_server(
                model_name=args.model,
                server_path=args.server,
                port=args.port,
                ctx=args.ctx,
                cache_type_k=args.turbo_k,
                cache_type_v=args.turbo_v,
                flash_attn=getattr(args, "flash_attn", False),
                sparsity=getattr(args, "sparsity", 0.0),
                num_threads=getattr(args, "threads", 0),
                wait_ready=not getattr(args, "no_wait", False),
                ready_timeout=getattr(args, "timeout", 120),
                extra_args=getattr(args, "extra_args", None),
            )
            # Show how to use
            print(dim("  Use with AgentKthx:"))
            _cmd1 = f"agentkthx run --backend llama-server --model {args.model} \"<prompt>\""
            _cmd2 = f"OLLAMA_BASE_URL=http://localhost:{state.port} agentkthx run \"<prompt>\""
            print(f"    {cyan(_cmd1)}")
            print(f"    {cyan(_cmd2)}")
            print()
            return 0
        except FileNotFoundError as e:
            print(bright_red(f"Error: {e}"))
            return 1
        except RuntimeError as e:
            print(bright_red(f"Error: {e}"))
            return 1
        except ValueError as e:
            print(bright_red(f"Error: {e}"))
            return 1

    elif turbo_cmd == "stop":
        stopped = stop_server(force=getattr(args, "force", False))
        if not stopped:
            print(yellow("No TurboQuant server is running."))
            print()
        return 0

    elif turbo_cmd == "status":
        state = get_status()
        if state:
            print_status(state)
        else:
            print(yellow("No TurboQuant server is running."))
            print()
            print(dim("  Start one with:"))
            print(f"    {cyan('agentkthx turbo list')}        # see available models")
            print(f"    {cyan('agentkthx turbo start <model>')}  # start server")
            print()
        return 0

    return 1
