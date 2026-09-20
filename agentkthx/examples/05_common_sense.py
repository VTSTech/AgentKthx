#!/usr/bin/env python3
"""
examples/05_common_sense.py
---------------------------
Common sense reasoning tests (from BIG-bench).

Tests everyday knowledge and common sense understanding:
  • Physical properties and behaviors
  • Social norms and expectations
  • Practical reasoning

Usage:
  python examples/05_common_sense.py
  python examples/05_common_sense.py --model qwen2.5:0.5b --debug
  agentkthx test 05

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
    parser = argparse.ArgumentParser(description="AgentKthx Common Sense Tests")
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


# Common sense tests (from BIG-bench general_knowledge and commonsense_qa style)
TESTS = [
    # Physical properties
    {
        "category": "Physical",
        "prompt": "How many legs do horses have? Answer with a number.",
        "expected": "4",
        "type": "number",
    },
    {
        "category": "Physical",
        "prompt": "How many eyes do most spiders have? Answer with a number.",
        "expected": "8",
        "type": "number",
    },
    {
        "category": "Physical",
        "prompt": "How many tails does a cat have? Answer with a number.",
        "expected": "1",
        "type": "number",
    },
    {
        "category": "Physical",
        "prompt": "What is the shape of a wheel? Answer with one word.",
        "expected": "circle",
        "type": "exact",
    },
    {
        "category": "Physical",
        "prompt": "What color is the sky on a clear sunny day? Answer with one word.",
        "expected": "blue",
        "type": "exact",
    },
    
    # Everyday objects
    {
        "category": "Objects",
        "prompt": "What does a thermometer measure? Answer with one word.",
        "expected": "temperature",
        "type": "exact",
    },
    {
        "category": "Objects",
        "prompt": "What is rain made of? Answer with one word.",
        "expected": "water",
        "type": "exact",
    },
    {
        "category": "Objects",
        "prompt": "Which gas is generally used to fill balloons to make them rise? Answer with one word.",
        "expected": "helium",
        "type": "exact",
    },
    {
        "category": "Objects",
        "prompt": "Which liquid is usually used to fill swimming pools? Answer with one word.",
        "expected": "water",
        "type": "exact",
    },
    {
        "category": "Objects",
        "prompt": "What sound does a cat make? Answer with one word.",
        "expected": "meow",
        "type": "keyword",
    },
    
    # Social situations
    {
        "category": "Social",
        "prompt": "Where would you go to buy dinner? Answer with one word.",
        "expected": "restaurant",
        "type": "keyword",
    },
    {
        "category": "Social",
        "prompt": "Which professional would you consult if you have health issues? Answer with one word.",
        "expected": "doctor",
        "type": "exact",
    },
    {
        "category": "Social",
        "prompt": "What are people opening when they wake up? Answer with one word.",
        "expected": "eyes",
        "type": "exact",
    },
    {
        "category": "Social",
        "prompt": "Which organ do humans use to hear? Answer with one word.",
        "expected": "ears",
        "type": "exact",
    },
    {
        "category": "Social",
        "prompt": "What is the color of a human tongue? Answer with one word.",
        "expected": "red",
        "type": "exact",
    },
    
    # Practical reasoning
    {
        "category": "Practical",
        "prompt": "If I drop a glass on a concrete floor, what will happen? Answer in one word.",
        "expected": "break",
        "type": "keyword",
    },
    {
        "category": "Practical",
        "prompt": "Can you eat soup with a fork? Answer yes or no.",
        "expected": "no",
        "type": "exact",
    },
    {
        "category": "Practical",
        "prompt": "How many giraffes are in the average living room? Answer with a number.",
        "expected": "0",
        "type": "number",
    },
    {
        "category": "Practical",
        "prompt": "How many elephants can fit in a standard freezer? Answer with a number.",
        "expected": "0",
        "type": "number",
    },
    {
        "category": "Practical",
        "prompt": "If you're facing north and turn 90 degrees to your right, which direction are you facing? Answer with one word.",
        "expected": "east",
        "type": "exact",
    },
    
    # Animals and nature
    {
        "category": "Nature",
        "prompt": "What is a chicken hatched from? Answer with one word.",
        "expected": "egg",
        "type": "exact",
    },
    {
        "category": "Nature",
        "prompt": "What sound does a duck make? Answer with one word.",
        "expected": "quack",
        "type": "exact",
    },
    {
        "category": "Nature",
        "prompt": "What sound does a cow make? Answer with one word.",
        "expected": "moo",
        "type": "exact",
    },
    {
        "category": "Nature",
        "prompt": "Which plants predominantly comprise forests? Answer with one word.",
        "expected": "trees",
        "type": "exact",
    },
    {
        "category": "Nature",
        "prompt": "What is the largest animal in the world? Answer with one or two words.",
        "expected": "blue whale",
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
    """Check if response matches expected answer with flexible matching."""
    response_lower = response.lower().strip()
    expected_lower = expected.lower().strip()
    
    # Synonyms and acceptable alternatives
    synonyms = {
        # Animal sounds
        "meow": ["meow", "meoww", "miau", "mew", "purr", "cat sound"],
        "quack": ["quack", "quack quack", "duck sound"],
        "moo": ["moo", "mooo", "cow sound", "low"],
        # Objects
        "helium": ["helium", "helium gas", "gas", "lighter than air"],
        "thermometer": ["thermometer", "temperature", "temp"],
        # Social
        "restaurant": ["restaurant", "cafe", "diner", "eatery", "food place", "store"],
        "doctor": ["doctor", "physician", "medical", "health", "hospital", "clinic"],
        "eyes": ["eyes", "eye", "open eyes", "eyelids"],
        "ears": ["ears", "ear", "hear", "hearing"],
        "red": ["red", "pinkish", "pink"],
        # Practical
        "break": ["break", "broke", "broken", "shatter", "crack", "smash", "碎片", "破碎"],
        "east": ["east", "right", "e"],
        # Nature
        "egg": ["egg", "eggs", "hatch"],
        "trees": ["trees", "tree", "forest", "wood"],
        "blue whale": ["blue whale", "whale", "blue", "largest animal"],
    }
    
    if check_type == "exact":
        # Check for expected word or synonyms
        if expected_lower in response_lower:
            return True
        if expected_lower in synonyms:
            for syn in synonyms[expected_lower]:
                if syn in response_lower:
                    return True
        return False
    
    elif check_type == "keyword":
        # Check for expected word or synonyms
        if expected_lower in response_lower:
            return True
        if expected_lower in synonyms:
            for syn in synonyms[expected_lower]:
                if syn in response_lower:
                    return True
        # Also check if any keyword is present
        keywords = expected_lower.split()
        return any(kw in response_lower for kw in keywords)
    
    elif check_type == "number":
        resp_num = extract_number(response_lower)
        exp_num = extract_number(expected_lower)
        if resp_num and exp_num:
            try:
                return abs(float(resp_num) - float(exp_num)) < 0.5
            except ValueError:
                return False
        return False
    
    return False


def run_tests(model: str, backend, debug: bool = False,
              soul: str = None, soul_level: int = 2,
              force_react: bool = False,
              num_ctx: int = None, num_predict: int = None,
              temperature: float = None, top_p: float = None) -> dict:
    """Run common sense tests for a model."""
    print(f"\n{'='*60}")
    print(f"🧠 Common Sense Tests: {model}")
    print(f"{'='*60}")
    
    results = {"model": model, "passed": 0, "total": len(TESTS), "time": 0, "categories": {}}
    
    # Custom system prompt for common sense questions
    common_sense_prompt = """Answer common sense questions directly and briefly.

Instructions:
- Use one word when asked for one word
- Use numbers when asked for numbers
- Give the most direct, obvious answer
- Trust your common knowledge"""

    # Note: We create a fresh agent for each test to avoid memory contamination
    # This ensures wrong answers don't affect subsequent questions
    
    for test in TESTS:
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
            system_prompt=common_sense_prompt,
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
        if backend_name == "ollama":
            print("   Start with: ollama serve")
        return {"passed": 0, "total": len(TESTS), "time": 0, "exit_code": 1}
    
    print(f"\n⚛️ AgentKthx Common Sense Tests ({len(TESTS)} questions)")
    print(f"   Backend: {backend_name} ({backend.base_url})")
    print(f"   Model: {model}")
    if api_mode != 'openre':
        print(f"   API Mode: {api_mode}")
    if timeout:
        print(f"   Timeout: {timeout}s")
    
    result = run_tests(model, backend, args.debug,
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
        print(f"  {category:<15} {bar} {stats['passed']}/{stats['total']} ({pct:.0f}%)")
    
    # Overall
    pass_rate = result["passed"] / result["total"] * 100
    print(f"\n📊 Overall: {result['passed']}/{result['total']} ({pass_rate:.0f}%) in {result['time']:.1f}s")
    print(f"{'='*60}")
    
    result["exit_code"] = 0 if result["passed"] == result["total"] else 1
    return result


if __name__ == "__main__":
    result = main()
    sys.exit(result.get("exit_code", 0))