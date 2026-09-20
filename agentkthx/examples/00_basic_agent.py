#!/usr/bin/env python3
"""
examples/00_basic_agent.py
--------------------------
The simplest possible AgentKthx agent — no tools, just conversation.

Usage:
  python examples/00_basic_agent.py
  python examples/00_basic_agent.py --model qwen3:0.6b
  agentkthx test 00

Written by VTSTech — https://www.vts-tech.org
"""

import sys
import os
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentkthx import Agent, get_config
from agentkthx.backends import get_default_backend


def parse_args():
    parser = argparse.ArgumentParser(description="AgentKthx Basic Agent")
    parser.add_argument("-m", "--model", default=None, help="Model to use")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument("--backend", choices=["ollama", "bitnet", "llama-server"], default=None)
    parser.add_argument("--api", choices=["openre", "openai"], default="openre", dest="api_mode",
                       help="API mode: 'openre' (OpenResponses) or 'openai' (Chat-Completions (OpenAI))")
    parser.add_argument("--soul", default=None, help="Path to Soul Spec package")
    parser.add_argument("--soul-level", type=int, default=2, choices=[1, 2, 3],
                       help="Soul progressive disclosure level")
    parser.add_argument("--num-ctx", type=int, default=None,
                       help="Context window size in tokens")
    parser.add_argument("--num-predict", type=int, default=None,
                       help="Maximum tokens to generate")
    parser.add_argument("--temp", type=float, default=None, dest="temperature",
                       help="Sampling temperature 0.0-2.0")
    parser.add_argument("--top-p", type=float, default=None, dest="top_p",
                       help="Nucleus sampling probability 0.0-1.0")
    parser.add_argument("--force-react", action="store_true", help="Force ReAct mode for tool calling")
    parser.add_argument("--timeout", type=int, default=None,
                       help="Request timeout in seconds (default: 120)")
    return parser.parse_args()


def main():
    args = parse_args()
    config = get_config()
    
    # Get backend (respects AGENTKTHX_BACKEND env var)
    backend_name = args.backend or config.backend
    api_mode = getattr(args, 'api_mode', 'openre')
    timeout = getattr(args, 'timeout', None)
    backend = get_default_backend(backend_name, api_mode=api_mode, timeout=timeout)
    
    # Check if backend is running
    if not backend.is_running():
        print(f"❌ Backend not running at {backend.base_url}")
        if config.backend == "ollama":
            print("   Start with: ollama serve")
            print("   Or set OLLAMA_BASE_URL to your remote server")
        return 1
    
    print(f"✓ Backend running at {backend.base_url}")
    
    # Create a simple agent without tools
    agent = Agent(
        model=args.model or config.default_model,
        backend=backend,
        debug=args.debug or config.debug,
        soul=args.soul,
        soul_level=args.soul_level,
        force_react=getattr(args, 'force_react', False),
        num_ctx=getattr(args, 'num_ctx', None),
        num_predict=getattr(args, 'num_predict', None),
        temperature=getattr(args, 'temperature', None),
        top_p=getattr(args, 'top_p', None),
    )
    
    print(f"\n⚛️ AgentKthx Basic Agent")
    print(f"   Model: {args.model or config.default_model}")
    print(f"   Backend: {backend_name}")
    if api_mode != 'openre':
        print(f"   API Mode: {api_mode}")
    if timeout:
        print(f"   Timeout: {timeout}s")
    print("-" * 40)
    
    # Single question
    prompt = "What is the capital of France? Answer in one sentence."
    print(f"\nQ: {prompt}")
    
    result = agent.run(prompt)
    print(f"A: {result.final_answer}")
    
    # Multi-turn conversation (memory is retained)
    print("\n--- Multi-turn conversation ---")
    
    agent.run("My name is Alex.")
    result = agent.run("What's my name?")
    print(f"Agent remembers: {result.final_answer}")
    
    print(f"\n⏱️ Total time: {result.total_ms:.0f}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())