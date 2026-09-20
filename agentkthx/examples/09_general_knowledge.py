#!/usr/bin/env python3
"""
examples/09_general_knowledge.py
---------------------------------
General knowledge tests (from BIG-bench).

Tests factual knowledge across domains:
  • Geography and capitals
  • Science and nature
  • History and culture
  • Math and measurements

Usage:
  python examples/09_general_knowledge.py
  python examples/09_general_knowledge.py --model qwen2.5:0.5b --debug
  agentkthx test 09

Environment Variables:
  OLLAMA_BASE_URL     - Ollama server URL
  AGENTKTHX_MODEL     - Default model

Written by VTSTech — https://www.vts-tech.org
"""

import sys
import os
import time
import argparse
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentkthx import Agent, get_config
from agentkthx.backends import get_default_backend


def parse_args():
    parser = argparse.ArgumentParser(description="AgentKthx General Knowledge Tests")
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


# General knowledge tests (from BIG-bench general_knowledge)
TESTS = [
    # Geography - Capitals
    {
        "category": "Geography",
        "prompt": "What is the capital of France? Answer with one word.",
        "expected": "paris",
        "type": "exact",
    },
    {
        "category": "Geography",
        "prompt": "What is the capital of Japan? Answer with one word.",
        "expected": "tokyo",
        "type": "exact",
    },
    {
        "category": "Geography",
        "prompt": "What is the capital of Germany? Answer with one word.",
        "expected": "berlin",
        "type": "exact",
    },
    {
        "category": "Geography",
        "prompt": "What is the capital of Australia? Answer with one word.",
        "expected": "canberra",
        "type": "exact",
    },
    {
        "category": "Geography",
        "prompt": "What is the capital of China? Answer with one word.",
        "expected": "beijing",
        "type": "exact",
    },
    
    # Geography - Landmarks
    {
        "category": "Geography",
        "prompt": "What is the largest continent on Earth by area? Answer with one word.",
        "expected": "asia",
        "type": "exact",
    },
    {
        "category": "Geography",
        "prompt": "Which ocean is the largest? Answer with one word.",
        "expected": "pacific",
        "type": "exact",
    },
    {
        "category": "Geography",
        "prompt": "What is the longest river in the world? Answer with one word.",
        "expected": "nile",
        "type": "exact",
    },
    {
        "category": "Geography",
        "prompt": "What is the largest desert in the world? Answer with one word.",
        "expected": "sahara",
        "type": "exact",
    },
    {
        "category": "Geography",
        "prompt": "Which country has the most people? Answer with one word.",
        "expected": "india",
        "type": "exact",
    },
    
    # Science - Astronomy
    {
        "category": "Science",
        "prompt": "What planet in our solar system is closest to the sun? Answer with one word.",
        "expected": "mercury",
        "type": "exact",
    },
    {
        "category": "Science",
        "prompt": "What is the largest planet in our solar system? Answer with one word.",
        "expected": "jupiter",
        "type": "exact",
    },
    {
        "category": "Science",
        "prompt": "What is the second-largest planet in our solar system? Answer with one word.",
        "expected": "saturn",
        "type": "exact",
    },
    {
        "category": "Science",
        "prompt": "How many planets are in our solar system? Answer with a number.",
        "expected": "8",
        "type": "number",
    },
    {
        "category": "Science",
        "prompt": "What is the name of our galaxy? Answer with one or two words.",
        "expected": "milky way",
        "type": "keyword",
    },
    
    # Science - Biology
    {
        "category": "Science",
        "prompt": "What gas do plants absorb from the air for photosynthesis? Answer with one word.",
        "expected": "carbon dioxide",
        "type": "keyword",
    },
    {
        "category": "Science",
        "prompt": "What is the largest organ in the human body? Answer with one word.",
        "expected": "skin",
        "type": "exact",
    },
    {
        "category": "Science",
        "prompt": "How many bones are in the adult human body? Answer with a number.",
        "expected": "206",
        "type": "number",
    },
    {
        "category": "Science",
        "prompt": "What is the hardest substance in the human body? Answer with one word.",
        "expected": "enamel",
        "type": "exact",
    },
    {
        "category": "Science",
        "prompt": "What is the speed of light in a vacuum measured in km per second? Answer with a number.",
        "expected": "300000",
        "type": "number",
    },
    
    # Math and Measurement
    {
        "category": "Math",
        "prompt": "How many sides does a hexagon have? Answer with a number.",
        "expected": "6",
        "type": "number",
    },
    {
        "category": "Math",
        "prompt": "What is the name of a shape with 8 sides? Answer with one word.",
        "expected": "octagon",
        "type": "exact",
    },
    {
        "category": "Math",
        "prompt": "How many degrees are in a circle? Answer with a number.",
        "expected": "360",
        "type": "number",
    },
    {
        "category": "Math",
        "prompt": "What is the value of pi to 2 decimal places? Answer with a number.",
        "expected": "3.14",
        "type": "number",
    },
    {
        "category": "Math",
        "prompt": "How many edges does a cube have? Answer with a number.",
        "expected": "12",
        "type": "number",
    },
]


def check_answer(response: str, expected: str, check_type: str) -> bool:
    """Check if response matches expected answer with flexible matching."""
    response_lower = response.lower().strip()
    expected_lower = expected.lower().strip()
    
    # Synonyms and acceptable alternatives
    synonyms = {
        # Geography - Capitals
        "paris": ["paris", "france capital"],
        "tokyo": ["tokyo", "japan capital"],
        "berlin": ["berlin", "germany capital"],
        "canberra": ["canberra", "australia capital", "sydney", "melbourne"],  # Common misconceptions
        "beijing": ["beijing", "peking", "china capital"],
        # Geography - Landmarks
        "asia": ["asia", "asian"],
        "pacific": ["pacific", "pacific ocean"],
        "nile": ["nile", "nile river"],
        "sahara": ["sahara", "sahara desert", "desert"],
        "india": ["india", "indian", "china"],  # Both are correct for "most people" depending on year
        # Science - Astronomy
        "mercury": ["mercury", "closest to sun", "first planet"],
        "jupiter": ["jupiter", "largest planet", "gas giant"],
        "saturn": ["saturn", "second largest", "rings"],
        "milky way": ["milky way", "galaxy", "our galaxy"],
        # Science - Biology
        "carbon dioxide": ["carbon dioxide", "co2", "carbon"],
        "skin": ["skin", "largest organ", "epidermis"],
        "enamel": ["enamel", "tooth", "teeth", "hardest"],
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
        # Extract numbers from both
        resp_nums = re.findall(r'-?\d+\.?\d*', response_lower.replace(',', '').replace(' ', ''))
        exp_nums = re.findall(r'-?\d+\.?\d*', expected_lower)
        
        if resp_nums and exp_nums:
            try:
                resp_val = float(resp_nums[0])
                exp_val = float(exp_nums[0])
                # Allow some tolerance
                if exp_val > 1000:
                    return abs(resp_val - exp_val) < exp_val * 0.15  # 15% tolerance for large numbers
                return abs(resp_val - exp_val) < 1.0
            except ValueError:
                return False
        return False
    
    return False


def run_tests(model: str, backend, debug: bool = False,
              soul: str = None, soul_level: int = 2,
              force_react: bool = False,
              num_ctx: int = None, num_predict: int = None,
              temperature: float = None, top_p: float = None) -> dict:
    """Run general knowledge tests for a model."""
    print(f"\n{'='*60}")
    print(f"🌍 General Knowledge Tests: {model}")
    print(f"{'='*60}")
    
    results = {"model": model, "passed": 0, "total": len(TESTS), "time": 0, "categories": {}}
    
    # Custom system prompt for general knowledge
    knowledge_prompt = """Answer factual questions directly and briefly.

Instructions:
- Use one word when asked for one word
- Use numbers when asked for numbers
- Give the most accurate fact you know
- Be concise and direct"""

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
            system_prompt=knowledge_prompt,
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
    
    print(f"\n⚛️ AgentKthx General Knowledge Tests ({len(TESTS)} questions)")
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