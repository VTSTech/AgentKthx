#!/usr/bin/env python3
"""
examples/11_analogical_reasoning.py
-----------------------------------
Analogical reasoning tests.

Tests ability to understand relationships and apply patterns:
  • Verbal analogies
  • Semantic relationships
  • Pattern completion
  • Relational mapping

Usage:
  python examples/11_analogical_reasoning.py
  python examples/11_analogical_reasoning.py --model qwen2.5:0.5b --debug
  agentkthx test 11

Environment Variables:
  OLLAMA_BASE_URL     - Ollama server URL
  AGENTKTHX_MODEL     - Default model

Written by VTSTech — https://www.vts-tech.org
"""

import sys
import os
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentkthx import Agent, get_config
from agentkthx.backends import get_default_backend


def parse_args():
    parser = argparse.ArgumentParser(description="AgentKthx Analogical Reasoning Tests")
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


# Analogical reasoning tests (inspired by BIG-bench analogical_similarity)
TESTS = [
    # Part-whole relationships
    {
        "category": "Part-Whole",
        "prompt": "Wheel is to car as wing is to ___. Answer with one word.",
        "expected": "plane",
        "type": "keyword",
    },
    {
        "category": "Part-Whole",
        "prompt": "Page is to book as scene is to ___. Answer with one word.",
        "expected": "movie",
        "type": "keyword",
    },
    {
        "category": "Part-Whole",
        "prompt": "Finger is to hand as toe is to ___. Answer with one word.",
        "expected": "foot",
        "type": "exact",
    },
    {
        "category": "Part-Whole",
        "prompt": "Branch is to tree as arm is to ___. Answer with one word.",
        "expected": "body",
        "type": "exact",
    },
    {
        "category": "Part-Whole",
        "prompt": "Letter is to word as note is to ___. Answer with one word.",
        "expected": "song",
        "type": "keyword",
    },
    
    # Opposite relationships
    {
        "category": "Opposites",
        "prompt": "Hot is to cold as up is to ___. Answer with one word.",
        "expected": "down",
        "type": "exact",
    },
    {
        "category": "Opposites",
        "prompt": "Big is to small as tall is to ___. Answer with one word.",
        "expected": "short",
        "type": "exact",
    },
    {
        "category": "Opposites",
        "prompt": "Love is to hate as friend is to ___. Answer with one word.",
        "expected": "enemy",
        "type": "exact",
    },
    {
        "category": "Opposites",
        "prompt": "Light is to dark as day is to ___. Answer with one word.",
        "expected": "night",
        "type": "exact",
    },
    {
        "category": "Opposites",
        "prompt": "Begin is to end as start is to ___. Answer with one word.",
        "expected": "finish",
        "type": "exact",
    },
    
    # Function relationships
    {
        "category": "Function",
        "prompt": "Pen is to write as knife is to ___. Answer with one word.",
        "expected": "cut",
        "type": "exact",
    },
    {
        "category": "Function",
        "prompt": "Phone is to call as email is to ___. Answer with one word.",
        "expected": "message",
        "type": "keyword",
    },
    {
        "category": "Function",
        "prompt": "Key is to lock as password is to ___. Answer with one word.",
        "expected": "account",
        "type": "keyword",
    },
    {
        "category": "Function",
        "prompt": "Thermometer is to temperature as scale is to ___. Answer with one word.",
        "expected": "weight",
        "type": "exact",
    },
    {
        "category": "Function",
        "prompt": "Camera is to photo as microphone is to ___. Answer with one word.",
        "expected": "sound",
        "type": "keyword",
    },
    
    # Category relationships
    {
        "category": "Category",
        "prompt": "Apple is to fruit as carrot is to ___. Answer with one word.",
        "expected": "vegetable",
        "type": "exact",
    },
    {
        "category": "Category",
        "prompt": "Dog is to animal as rose is to ___. Answer with one word.",
        "expected": "plant",
        "type": "exact",
    },
    {
        "category": "Category",
        "prompt": "Sedan is to car as laptop is to ___. Answer with one word.",
        "expected": "computer",
        "type": "exact",
    },
    {
        "category": "Category",
        "prompt": "Sparrow is to bird as salmon is to ___. Answer with one word.",
        "expected": "fish",
        "type": "exact",
    },
    {
        "category": "Category",
        "prompt": "Violin is to instrument as soccer is to ___. Answer with one word.",
        "expected": "sport",
        "type": "exact",
    },
    
    # Cause-effect relationships
    {
        "category": "Cause-Effect",
        "prompt": "Rain is to wet as sun is to ___. Answer with one word.",
        "expected": "warm",
        "type": "keyword",
    },
    {
        "category": "Cause-Effect",
        "prompt": "Study is to learn as exercise is to ___. Answer with one word.",
        "expected": "strength",
        "type": "keyword",
    },
    {
        "category": "Cause-Effect",
        "prompt": "Fire is to burn as ice is to ___. Answer with one word.",
        "expected": "freeze",
        "type": "keyword",
    },
    {
        "category": "Cause-Effect",
        "prompt": "Work is to tired as eat is to ___. Answer with one word.",
        "expected": "full",
        "type": "keyword",
    },
    {
        "category": "Cause-Effect",
        "prompt": "Practice is to skill as investment is to ___. Answer with one word.",
        "expected": "wealth",
        "type": "keyword",
    },
]


def check_answer(response: str, expected: str, check_type: str) -> bool:
    """Check if response matches expected answer."""
    response_lower = response.lower().strip()
    expected_lower = expected.lower().strip()
    
    # Synonyms and acceptable alternatives for analogical reasoning
    synonyms = {
        "plane": ["plane", "airplane", "aircraft", "jet"],
        "movie": ["movie", "film", "play", "theater"],
        "foot": ["foot", "feet"],
        "body": ["body", "person", "human"],
        "song": ["song", "music", "melody", "tune"],
        "down": ["down"],
        "short": ["short", "small"],
        "enemy": ["enemy", "foe", "rival"],
        "night": ["night"],
        "finish": ["finish", "end", "stop", "complete"],
        "cut": ["cut", "slice", "chop"],
        "message": ["message", "send", "communicate", "write", "text"],
        "account": ["account", "computer", "system", "login", "access"],
        "weight": ["weight", "mass", "heavy"],
        "sound": ["sound", "audio", "voice", "record"],
        "vegetable": ["vegetable", "veg"],
        "plant": ["plant", "flower"],
        "computer": ["computer", "pc", "device"],
        "fish": ["fish"],
        "sport": ["sport", "game"],
        "warm": ["warm", "hot", "heat", "bright"],
        "strength": ["strength", "strong", "fit", "muscle", "health"],
        "freeze": ["freeze", "cold", "frozen", "chill"],
        "full": ["full", "satisfied", "stuffed"],
        "wealth": ["wealth", "money", "profit", "return", "gain"],
    }
    
    if check_type == "exact":
        # Check for exact match or synonym
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
    
    return False


def run_tests(model: str, backend, debug: bool = False,
              soul: str = None, soul_level: int = 2,
              force_react: bool = False,
              num_ctx: int = None, num_predict: int = None,
              temperature: float = None, top_p: float = None) -> dict:
    """Run analogical reasoning tests for a model."""
    print(f"\n{'='*60}")
    print(f"🔄 Analogical Reasoning Tests: {model}")
    print(f"{'='*60}")
    
    results = {"model": model, "passed": 0, "total": len(TESTS), "time": 0, "categories": {}}
    
    # Custom system prompt for analogical reasoning
    analogy_prompt = """Complete analogies by understanding the relationship pattern.

Instructions:
- Identify the relationship between the first pair
- Apply the same relationship to find the missing word
- Use one word when asked for one word
- Think about: part-whole, opposites, function, category, cause-effect"""

    # Note: We create a fresh agent for each test to avoid memory contamination
    
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
            max_steps=1,
            debug=debug,
            system_prompt=analogy_prompt,
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
        return {"passed": 0, "total": len(TESTS), "time": 0, "exit_code": 1}
    
    print(f"\n⚛️ AgentKthx Analogical Reasoning Tests ({len(TESTS)} questions)")
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
    
    print(f"\n{'='*60}")
    print("📊 Results by Category")
    print(f"{'='*60}")
    
    for category, stats in result["categories"].items():
        pct = stats["passed"] / stats["total"] * 100
        bar = "█" * stats["passed"] + "░" * (stats["total"] - stats["passed"])
        print(f"  {category:<15} {bar} {stats['passed']}/{stats['total']} ({pct:.0f}%)")
    
    pass_rate = result["passed"] / result["total"] * 100
    print(f"\n📊 Overall: {result['passed']}/{result['total']} ({pass_rate:.0f}%) in {result['time']:.1f}s")
    print(f"{'='*60}")
    
    result["exit_code"] = 0 if result["passed"] == result["total"] else 1
    return result


if __name__ == "__main__":
    result = main()
    sys.exit(result.get("exit_code", 0))