"""`agentkthx modelfile` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse

from ...backends import get_backend
from ...colors import bold, dim, cyan, red
from ...config import get_config

def cmd_modelfile(args: argparse.Namespace) -> int:
    """Show model's Modelfile system prompt and other info."""
    from ...backends import OllamaBackend

    config = get_config()
    backend_name = args.backend or config.backend
    backend = get_backend(backend_name)

    if not isinstance(backend, OllamaBackend):
        print(f"{red('Error:')} Modelfile command requires Ollama backend")
        return 1

    if not backend.is_running():
        print(f"{red('✗')}  Ollama is not running. Start it with: {cyan('ollama serve')}")
        return 1

    model = args.model or config.default_model
    print(bold(f"\n⚛️ AgentKthx Modelfile") + dim(" · Written by VTSTech · https://kthx.vts-tech.org"))
    print()

    try:
        info = backend.get_model_info(model)
    except Exception as e:
        print(f"{red('✗')}  Could not get info for model '{model}': {e}")
        return 1

    # Display model information
    print(bold(f"Model: {model}"))
    print(dim("─" * 70))
    print()

    # System prompt from Modelfile
    system_prompt = info.get("system")
    if system_prompt:
        print(cyan("SYSTEM PROMPT (from Modelfile):"))
        print()
        print(system_prompt)
        print()
    else:
        print(dim("(No SYSTEM prompt defined in Modelfile)"))
        print()

    # Template
    template = info.get("template")
    if template:
        print(cyan("TEMPLATE:"))
        print()
        print(template)
        print()

    # Parameters
    params = info.get("parameters", "")
    if params:
        print(cyan("PARAMETERS:"))
        print()
        for line in params.strip().split("\n"):
            if line.strip():
                print(dim(f"  {line.strip()}"))
        print()

    # Details
    details = info.get("details", {})
    if details:
        print(cyan("DETAILS:"))
        print()
        for key, value in details.items():
            print(dim(f"  {key}: {value}"))
        print()

    return 0
