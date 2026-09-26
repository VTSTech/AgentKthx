#!/usr/bin/env python3
"""
examples/03_reasoning_test.py
-----------------------------
Logic and reasoning tests (inspired by Big-Bench Hard).

Tests:
  • Logical deduction
  • Common sense reasoning
  • Multi-step reasoning
  • Pattern recognition
  • Counter-intuitive reasoning

Usage:
  python examples/03_reasoning_test.py
  python examples/03_reasoning_test.py --model qwen2.5:0.5b --debug
  agentkthx test 03

Environment Variables:
  OLLAMA_BASE_URL     - Ollama server URL
  AGENTKTHX_MODEL     - Default model

Written by VTSTech — https://www.vts-tech.org
"""

import sys
import os
import time
import re
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentkthx import Agent, get_config
from agentkthx.backends import get_default_backend


def parse_args():
    parser = argparse.ArgumentParser(description="AgentKthx Reasoning Tests")
    parser.add_argument("-m", "--model", default=None, help="Model to test")
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


# Reasoning tests (no tools, pure reasoning)
REASONING_TESTS = [
    # Logical Deduction
    {
        "category": "Logical Deduction",
        "prompt": "All cats are animals. All animals need water. Does a cat need water? Answer yes or no.",
        "expected": "yes",
        "type": "exact",
    },
    {
        "category": "Logical Deduction",
        "prompt": "If all roses are flowers, and some flowers are red, can we conclude that some roses are red? Answer yes, no, or uncertain.",
        "expected": "uncertain",
        "type": "exact",
    },
    
    # Common Sense Reasoning
    {
        "category": "Common Sense",
        "prompt": "If I drop a glass on a concrete floor, what will happen? Answer in one sentence.",
        "expected": "break",
        "type": "keyword",
    },
    {
        "category": "Common Sense",
        "prompt": "Can you eat soup with a fork? Answer yes or no.",
        "expected": "no",
        "type": "exact",
    },
    
    # Multi-step Reasoning
    {
        "category": "Multi-step",
        "prompt": "John is taller than Mary. Mary is taller than Sue. Is John taller than Sue? Answer yes or no.",
        "expected": "yes",
        "type": "exact",
    },
    {
        "category": "Multi-step",
        "prompt": "A train leaves station A at 2 PM and arrives at station B at 5 PM. The distance is 180 miles. What is the average speed in mph? Just give the number.",
        "expected": "60",
        "type": "number",
    },
    
    # Pattern Recognition
    {
        "category": "Pattern",
        "prompt": "What comes next in the sequence: 2, 4, 6, 8, ? Just give the number.",
        "expected": "10",
        "type": "number",
    },
    {
        "category": "Pattern",
        "prompt": "What comes next: A, C, E, G, ? Just give the letter.",
        "expected": "I",
        "type": "exact",
    },
    
    # Counter-intuitive Reasoning
    {
        "category": "Counter-intuitive",
        "prompt": "A bat and ball cost $1.10 total. The bat costs $1 more than the ball. How much does the ball cost in dollars? Just give the number.",
        "expected": "0.05",
        "type": "number",
    },
    {
        "category": "Counter-intuitive",
        "prompt": "It takes 10 minutes for 10 machines to make 10 widgets. How many minutes would it take 100 machines to make 100 widgets? Just give the number.",
        "expected": "10",
        "type": "number",
    },
    
    # Spatial Reasoning
    {
        "category": "Spatial",
        "prompt": "If you're facing north and turn 90 degrees to your right, which direction are you facing? Answer with one word.",
        "expected": "east",
        "type": "exact",
    },
    {
        "category": "Spatial",
        "prompt": "A cube has how many edges? Just give the number.",
        "expected": "12",
        "type": "number",
    },
    
    # Causal Reasoning
    {
        "category": "Causal",
        "prompt": "If it rained last night and the ground is wet, which is the cause and which is the effect? Answer with: rain is cause, wet ground is effect.",
        "expected": "rain is cause",
        "type": "keyword",
    },
    
    # Comparative Reasoning
    {
        "category": "Comparative",
        "prompt": "Which is heavier: a pound of feathers or a pound of lead? Answer: they weigh the same, feathers are heavier, or lead is heavier.",
        "expected": "same",
        "type": "keyword",
    },
]


def normalize_answer(text: str) -> str:
    """Normalize answer for comparison."""
    return text.lower().strip()


def extract_number(text: str) -> str:
    """Extract number from text."""
    match = re.search(r'-?\d+\.?\d*', text.replace(',', ''))
    return match.group(0) if match else ""


def check_answer(response: str, expected: str, check_type: str) -> bool:
    """Check if response matches expected answer."""
    response_lower = response.lower()
    expected_lower = expected.lower()
    
    if check_type == "exact":
        # Check if expected is in response
        return expected_lower in response_lower
    
    elif check_type == "keyword":
        # Check for keyword presence
        keywords = expected_lower.split()
        return any(kw in response_lower for kw in keywords)
    
    elif check_type == "number":
        # Extract and compare numbers
        resp_num = extract_number(response_lower)
        exp_num = extract_number(expected_lower)
        if resp_num and exp_num:
            try:
                return abs(float(resp_num) - float(exp_num)) < 0.01
            except ValueError:
                return False
        return False
    
    return False


def run_reasoning_test(model: str, backend, debug: bool = False,
                       soul: str = None, soul_level: int = 2,
                       force_react: bool = False,
                       num_ctx: int = None, num_predict: int = None,
                       temperature: float = None, top_p: float = None) -> dict:
    """Run reasoning tests for a model."""
    print(f"\n{'='*60}")
    print(f"🧠 Reasoning Tests: {model}")
    print(f"{'='*60}")
    
    results = {"model": model, "passed": 0, "total": len(REASONING_TESTS), "time": 0, "categories": {}}
    
    # Note: We create a fresh agent for each test to avoid memory contamination
    
    for test in REASONING_TESTS:
        category = test["category"]
        prompt = test["prompt"]
        expected = test["expected"]
        check_type = test["type"]
        
        print(f"\n📋 [{category}] {prompt}...")
        
        # Create fresh agent for each test (isolates memory)
        agent = Agent(
            model=model,
            backend=backend,
            max_steps=1,  # Single-step for reasoning (no tools)
            debug=debug,
            soul=soul,
            soul_level=soul_level,
            force_react=force_react,
            num_ctx=num_ctx,
            num_predict=num_predict,
            temperature=temperature,
            top_p=top_p,
        )
        
        t0 = time.time()
        run = agent.run(prompt)
        elapsed = time.time() - t0
        results["time"] += elapsed
        
        response = run.final_answer
        passed = check_answer(response, expected, check_type)
        
        # Track by category
        if category not in results["categories"]:
            results["categories"][category] = {"passed": 0, "total": 0}
        results["categories"][category]["total"] += 1
        if passed:
            results["categories"][category]["passed"] += 1
        
        results["passed"] += int(passed)
        
        status = "✅" if passed else "❌"
        print(f"  {status} Expected: {expected} | Got: {response}")
        print(f"     {elapsed:.1f}s")
    
    return results


def main():
    args = parse_args()
    config = get_config()
    
    model = args.model or config.default_model
    backend_name = args.backend or config.backend
    api_mode = getattr(args, 'api_mode', 'openre')
    timeout = getattr(args, 'timeout', None)
    backend = get_default_backend(backend_name, api_mode=api_mode, timeout=timeout)
    
    if not backend.is_running():
        print(f"❌ {backend_name.capitalize()} not running at {backend.base_url}")
        return 1
    
    print(f"\n⚛️ AgentKthx Reasoning Tests")
    print(f"   Backend: {backend_name} ({backend.base_url})")
    print(f"   Model: {model}")
    print(f"   Tests: {len(REASONING_TESTS)}")
    if api_mode != 'openre':
        print(f"   API Mode: {api_mode}")
    if timeout:
        print(f"   Timeout: {timeout}s")
    
    result = run_reasoning_test(model, backend, args.debug,
                                soul=args.soul, soul_level=args.soul_level,
                                force_react=getattr(args, 'force_react', False),
                                num_ctx=getattr(args, 'num_ctx', None),
                                num_predict=getattr(args, 'num_predict', None),
                                temperature=getattr(args, 'temperature', None),
                                top_p=getattr(args, 'top_p', None))
    
    # Print category breakdown
    print(f"\n{'='*60}")
    print("📊 Results by Category")
    print(f"{'='*60}")
    
    for category, stats in result["categories"].items():
        pct = stats["passed"] / stats["total"] * 100
        bar = "█" * stats["passed"] + "░" * (stats["total"] - stats["passed"])
        print(f"  {category:<20} {bar} {stats['passed']}/{stats['total']} ({pct:.0f}%)")
    
    # Overall
    pass_rate = result["passed"] / result["total"] * 100
    print(f"\n📊 Overall: {result['passed']}/{result['total']} ({pass_rate:.0f}%) in {result['time']:.1f}s")
    print(f"{'='*60}")
    
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    sys.exit(main())