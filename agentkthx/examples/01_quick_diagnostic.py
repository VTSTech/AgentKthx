#!/usr/bin/env python3
"""
examples/01_quick_diagnostic.py
-------------------------------
Quick 5-question diagnostic for rapid iteration and debugging.

Tests common failure modes:
  Q1: Simple math (basic calculator tool usage)
  Q2: Multi-step reasoning (observation handling)
  Q3: Division with fraction (precision)
  Q4: Word problem (natural language → expression)
  Q5: Time calculation (practical reasoning)

Usage:
  python examples/01_quick_diagnostic.py
  python examples/01_quick_diagnostic.py --model qwen2.5:0.5b --debug
  agentkthx test 01

Environment Variables:
  OLLAMA_BASE_URL     - Ollama server URL
  AGENTKTHX_MODEL     - Default model
  AGENTKTHX_BACKEND   - Backend to use (ollama/bitnet)

Written by VTSTech — https://www.vts-tech.org
"""

import sys
import os
import time
import re
import unicodedata
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentkthx import Agent, get_config, __version__, __status__
from agentkthx.backends import get_backend
from agentkthx.tools import make_builtin_registry


def parse_args():
    parser = argparse.ArgumentParser(description="AgentKthx Quick Diagnostic")
    parser.add_argument("-m", "--model", default=None, help="Model to test")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument("--backend", choices=["ollama", "bitnet", "llama-server"], default=None)
    parser.add_argument("--api", choices=["openre", "openai"], default="openre", dest="api_mode",
                       help="API mode: 'openre' (OpenResponses) or 'openai' (Chat-Completions (OpenAI))")
    parser.add_argument("--force-react", action="store_true", help="Force ReAct mode for tool calling")
    parser.add_argument("--soul", default=None, help="Path to Soul Spec package")
    parser.add_argument("--soul-level", type=int, default=2, choices=[1, 2, 3],
                       help="Soul progressive disclosure level (1=quick, 2=full, 3=deep)")
    parser.add_argument("--timeout", type=int, default=None,
                       help="Request timeout in seconds (default: 120)")
    parser.add_argument("--warmup", action="store_true",
                       help="Send warmup request before testing (avoids cold start timeout)")
    parser.add_argument("--num-ctx", type=int, default=None,
                       help="Context window size in tokens")
    parser.add_argument("--num-predict", type=int, default=None,
                       help="Maximum tokens to generate")
    parser.add_argument("--temp", type=float, default=None, dest="temperature",
                       help="Sampling temperature 0.0-2.0")
    parser.add_argument("--top-p", type=float, default=None, dest="top_p",
                       help="Nucleus sampling probability 0.0-1.0")
    return parser.parse_args()


# Quick 5-question diagnostic
TESTS = [
    # Q1: Simple math - basic calculator tool usage
    ("Q1: Simple Math", "What is 15 plus 27? Use the calculator tool.", ["calculator"], "42"),
    
    # Q2: Multi-step reasoning (8*7-5=51)
    ("Q2: Multi-step", "Calculate 8 times 7 minus 5. Use the calculator.", ["calculator"], "51"),
    
    # Q3: Division with fraction - precision test
    ("Q3: Division", "What is 17 divided by 4? Use the calculator.", ["calculator"], "4.25"),
    
    # Q4: Word problem (24-8-6=10)
    ("Q4: Word Problem", "A store has 24 apples. They sell 8 in the morning and 6 in the afternoon. How many apples are left?", ["calculator"], "10"),
    
    # Q5: Time calculation (5-9+12=8)
    ("Q5: Time Calc", "A store opens at 9 AM and closes at 5 PM. How many hours is it open?", ["calculator"], "8"),
]


def normalize_text(text: str) -> str:
    """Normalize text for comparison."""
    normalized = unicodedata.normalize('NFD', text.lower())
    without_accents = ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')
    return without_accents.strip()


def extract_number(text: str) -> str | None:
    """Extract the first number from text."""
    match = re.search(r'-?\d+\.?\d*', text)
    return match.group(0) if match else None


def run_diagnostic(model: str, backend, debug: bool = False, force_react: bool = False,
                   soul: str = None, soul_level: int = 2, timeout: int = None,
                   num_ctx: int = None, num_predict: int = None,
                   temperature: float = None, top_p: float = None) -> dict:
    """Run diagnostic tests for a model."""
    print(f"\n{'='*60}")
    print(f"🧪 Quick Diagnostic: {model}")
    print(f"{'='*60}")
    
    results = {"model": model, "passed": 0, "total": len(TESTS), "time": 0, "tests": {}}
    
    # System prompt: use soul's prompt if provided, otherwise use default calculator prompt
    if soul:
        system_prompt = None  # Soul will provide the system prompt
    else:
        system_prompt = """You are a helpful assistant with access to a calculator tool.
When asked to calculate something, ALWAYS use the calculator tool.
Pass the mathematical expression to the calculator (e.g., "15 + 27" or "8 * 7 - 5").
After getting the result, provide the final answer as a number."""
    
    for i, (test_name, prompt, tools, expected) in enumerate(TESTS):
        print(f"  {test_name}...", end="\n", flush=True)
        
        try:
            # Build tools
            tool_registry = make_builtin_registry().subset(tools) if tools else None
            
            agent = Agent(
                model=model,
                tools=tool_registry,
                backend=backend,
                max_steps=5,
                debug=debug,
                force_react=force_react,
                system_prompt=system_prompt,
                soul=soul,
                soul_level=soul_level,
                num_ctx=num_ctx,
                temperature=temperature,
                top_p=top_p,
                num_predict=num_predict,
            )
            
            t0 = time.time()
            run = agent.run(prompt)
            elapsed = time.time() - t0
            results["time"] += elapsed
            
            response = run.final_answer
            response_norm = normalize_text(response)
            expected_norm = normalize_text(expected)
            
            # Try exact match first
            passed = expected_norm in response_norm
            
            # If not found, try extracting numbers
            if not passed:
                resp_num = extract_number(response_norm)
                exp_num = extract_number(expected_norm)
                if resp_num and exp_num:
                    try:
                        passed = abs(float(resp_num) - float(exp_num)) < 0.01
                    except ValueError:
                        pass
            
            results["passed"] += int(passed)
            results["tests"][test_name] = {
                "passed": passed,
                "time": elapsed,
                "response": response,
                "expected": expected,
            }
            
            if passed:
                print(f"✅ ({elapsed:.1f}s)")
            else:
                print(f"❌ ({elapsed:.1f}s)")
                print(f"      Expected: {expected}, Got: {response}")
            
        except Exception as e:
            results["tests"][test_name] = {"passed": False, "error": str(e)[:100]}
            print(f"❌ ERROR: {str(e)[:50]}")
    
    pass_rate = results["passed"] / results["total"] * 100
    print(f"\n  📊 Result: {results['passed']}/{results['total']} ({pass_rate:.0f}%) in {results['time']:.1f}s")
    
    return results


def run_warmup(model: str, backend, timeout: int = None) -> bool:
    """Send a warmup request to load the model into memory.
    
    Uses a direct backend call with minimal prompt to avoid loading
    the full soul (saves ~8000 chars of system prompt processing).
    """
    print("🔥 Warming up model...", end=" ", flush=True)
    try:
        t0 = time.time()
        # Direct backend call - no Agent, no soul, minimal overhead
        response = backend.generate(
            model=model,
            messages=[{"role": "user", "content": "Say 'ok'"}],
            temperature=0.1,
            keep_alive="10m",
            max_tokens=10,
        )
        elapsed = time.time() - t0
        print(f"✅ ({elapsed:.1f}s)")
        return True
    except Exception as e:
        print(f"❌ {e}")
        return False


def main():
    args = parse_args()
    config = get_config()
    
    # Enable debug output
    if args.debug:
        os.environ["AGENTKTHX_DEBUG"] = "1"
    
    # Get model
    model = args.model or config.default_model
    
    # Get backend with timeout
    backend_name = args.backend or config.backend
    api_mode = getattr(args, 'api_mode', 'openre')
    timeout = getattr(args, 'timeout', None)
    backend = get_backend(backend_name, timeout=timeout, api_mode=api_mode)
    
    # Check if running
    if not backend.is_running():
        print(f"❌ {backend_name.capitalize()} not running at {backend.base_url}")
        if backend_name == "ollama":
            print("   Start with: ollama serve")
            print("   Or set OLLAMA_BASE_URL to your remote server")
        return {"passed": 0, "total": 1, "time": 0, "exit_code": 1}
    
    # Convert 0.3.3 to R03.3 format for display
    parts = __version__.split('.')
    display_version = f"R{int(parts[1]):02d}.{parts[2]}" if len(parts) >= 2 else __version__
    
    print(f"\n⚛️ AgentKthx {display_version} [{__status__}] Quick Diagnostic (5 questions)")
    print(f"   Backend: {backend_name} ({backend.base_url})")
    print(f"   Model: {model}")
    api_mode_display = {
        'openre': '[OpenResponses]',
        'openai': '[OpenAI] ChatCompletions'
    }.get(api_mode, api_mode)
    print(f"   API Mode: {api_mode_display}")
    if timeout:
        print(f"   Timeout: {timeout}s")
    if args.force_react:
        print(f"   Force ReAct: True")
    if args.soul:
        print(f"   Soul: {args.soul}")
    num_ctx = getattr(args, 'num_ctx', None)
    if num_ctx is None:
        num_ctx = getattr(config, 'num_ctx', None)
    if num_ctx:
        ctx_display = f"{num_ctx // 1024}K" if num_ctx >= 1024 else str(num_ctx)
        print(f"   Context: {ctx_display}")
    
    # Warmup if requested
    if getattr(args, 'warmup', False):
        if not run_warmup(model, backend, timeout):
            print("Warning: Warmup failed, continuing anyway...")
    
    result = run_diagnostic(model, backend, debug=args.debug, force_react=args.force_react,
                           soul=args.soul, soul_level=args.soul_level, timeout=timeout,
                           num_ctx=num_ctx, num_predict=getattr(args, 'num_predict', None),
                           temperature=getattr(args, 'temperature', None),
                           top_p=getattr(args, 'top_p', None))
    
    # Return granular results for test runner, exit_code for direct execution
    result["exit_code"] = 0 if result["passed"] == result["total"] else 1
    return result


if __name__ == "__main__":
    result = main()