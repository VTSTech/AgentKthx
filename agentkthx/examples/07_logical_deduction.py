#!/usr/bin/env python3
"""
examples/07_logical_deduction.py
--------------------------------
Logical deduction tests (from BIG-bench).

Tests logical reasoning abilities:
  • Syllogisms and deductive reasoning
  • Conditional reasoning
  • Quantifiers and set logic

Usage:
  python examples/07_logical_deduction.py
  python examples/07_logical_deduction.py --model qwen2.5:0.5b --debug
  agentkthx test 07

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
    parser = argparse.ArgumentParser(description="AgentKthx Logical Deduction Tests")
    parser.add_argument("-m", "--model", default=None, help="Model to test")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument("--backend", choices=["ollama", "bitnet", "llama-server"], default=None)
    parser.add_argument("--api", choices=["openre", "openai"], default="openre", dest="api_mode",
                       help="API mode: 'openre' (OpenResponses) or 'openai' (Chat-Completions (OpenAI))")
    parser.add_argument("--use-mf-sys", action="store_true", dest="use_modelfile_system",
                        help="Use the model's Modelfile system prompt instead of custom prompt")
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


# Logical deduction tests (inspired by BIG-bench)
TESTS = [
    # Simple syllogisms
    {
        "category": "Syllogisms",
        "prompt": "All cats are animals. All animals need water. Does a cat need water? Answer yes or no.",
        "expected": "yes",
        "type": "exact",
    },
    {
        "category": "Syllogisms",
        "prompt": "All dogs are mammals. All mammals are warm-blooded. Are dogs warm-blooded? Answer yes or no.",
        "expected": "yes",
        "type": "exact",
    },
    {
        "category": "Syllogisms",
        "prompt": "All birds have feathers. All eagles are birds. Do eagles have feathers? Answer yes or no.",
        "expected": "yes",
        "type": "exact",
    },
    {
        "category": "Syllogisms",
        "prompt": "All roses are flowers. Some flowers are red. Can we conclude that some roses are red? Answer yes, no, or uncertain.",
        "expected": "uncertain",
        "type": "exact",
    },
    {
        "category": "Syllogisms",
        "prompt": "All students study. Some students are lazy. Can we conclude that some lazy people study? Answer yes or no.",
        "expected": "yes",
        "type": "exact",
    },
    
    # Conditional reasoning
    {
        "category": "Conditionals",
        "prompt": "If it rains, the ground gets wet. It is raining. Is the ground wet? Answer yes or no.",
        "expected": "yes",
        "type": "exact",
    },
    {
        "category": "Conditionals",
        "prompt": "If it rains, the ground gets wet. The ground is wet. Did it definitely rain? Answer yes or no.",
        "expected": "no",
        "type": "exact",
    },
    {
        "category": "Conditionals",
        "prompt": "If you study, you will pass. You did not pass. Did you study? Answer yes or no.",
        "expected": "no",
        "type": "exact",
    },
    {
        "category": "Conditionals",
        "prompt": "If the light is on, someone is home. The light is off. Is anyone definitely not home? Answer yes or no.",
        "expected": "no",
        "type": "exact",
    },
    {
        "category": "Conditionals",
        "prompt": "If it snows, schools close. Schools are closed. Did it snow? Answer yes, no, or uncertain.",
        "expected": "uncertain",
        "type": "exact",
    },
    
    # Transitive reasoning
    {
        "category": "Transitive",
        "prompt": "John is taller than Mary. Mary is taller than Sue. Is John taller than Sue? Answer yes or no.",
        "expected": "yes",
        "type": "exact",
    },
    {
        "category": "Transitive",
        "prompt": "A is bigger than B. B is bigger than C. C is bigger than D. Is A bigger than D? Answer yes or no.",
        "expected": "yes",
        "type": "exact",
    },
    {
        "category": "Transitive",
        "prompt": "X is older than Y. Y is older than Z. Is Z older than X? Answer yes or no.",
        "expected": "no",
        "type": "exact",
    },
    {
        "category": "Transitive",
        "prompt": "Team A beat Team B. Team B beat Team C. Can we conclude that Team A would beat Team C? Answer yes or no.",
        "expected": "no",
        "type": "exact",
    },
    {
        "category": "Transitive",
        "prompt": "Alice arrived before Bob. Bob arrived before Carol. Who arrived last? Answer with one name.",
        "expected": "carol",
        "type": "exact",
    },
    
    # Quantifiers
    {
        "category": "Quantifiers",
        "prompt": "All apples are fruits. Is every apple a fruit? Answer yes or no.",
        "expected": "yes",
        "type": "exact",
    },
    {
        "category": "Quantifiers",
        "prompt": "Some birds cannot fly. Does this mean all birds can fly? Answer yes or no.",
        "expected": "no",
        "type": "exact",
    },
    {
        "category": "Quantifiers",
        "prompt": "No fish are mammals. Are any fish mammals? Answer yes or no.",
        "expected": "no",
        "type": "exact",
    },
    {
        "category": "Quantifiers",
        "prompt": "Every student passed. Did all students pass? Answer yes or no.",
        "expected": "yes",
        "type": "exact",
    },
    {
        "category": "Quantifiers",
        "prompt": "Not all cars are red. Are some cars not red? Answer yes or no.",
        "expected": "yes",
        "type": "exact",
    },
    
    # Counter-intuitive logic
    {
        "category": "Counter-intuitive",
        "prompt": "A bat and ball cost $1.10 total. The bat costs $1 more than the ball. How much does the ball cost in cents? Just give the number.",
        "expected": "5",
        "type": "number",
    },
    {
        "category": "Counter-intuitive",
        "prompt": "It takes 10 machines 10 minutes to make 10 widgets. How many minutes would it take 100 machines to make 100 widgets? Just give the number.",
        "expected": "10",
        "type": "number",
    },
    {
        "category": "Counter-intuitive",
        "prompt": "In a lake, there is a patch of lily pads. Every day, the patch doubles in size. If it takes 48 days for the patch to cover the entire lake, how many days would it take to cover half the lake? Just give the number.",
        "expected": "47",
        "type": "number",
    },
    {
        "category": "Counter-intuitive",
        "prompt": "A snail climbs up a 10-foot pole. Each day it climbs 3 feet, but each night it slides down 2 feet. How many days does it take to reach the top? Just give the number.",
        "expected": "8",
        "type": "number",
    },
    {
        "category": "Counter-intuitive",
        "prompt": "If you have 3 socks in a drawer (black, white, black), what is the minimum number you must pull out to guarantee a matching pair? Just give the number.",
        "expected": "3",
        "type": "number",
    },
]


def extract_number(response: str) -> str:
    """Extract number from text."""
    import re
    match = re.search(r'-?\d+\.?\d*', response.replace(',', ''))
    return match.group(0) if match else ""


def check_answer(response: str, expected: str, check_type: str) -> bool:
    """Check if response matches expected answer with flexible matching."""
    response_lower = response.lower().strip()
    expected_lower = expected.lower().strip()
    
    # Synonyms and acceptable alternatives
    synonyms = {
        # Transitive reasoning - names
        "carol": ["carol", "last", "third"],
        # Quantifiers
        "uncertain": ["uncertain", "maybe", "possibly", "not sure", "cannot determine", "unknown"],
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


def run_tests(model: str, backend, debug: bool = False, use_mf_sys: bool = False,
              soul: str = None, soul_level: int = 2,
              force_react: bool = False,
              num_ctx: int = None, num_predict: int = None,
              temperature: float = None, top_p: float = None) -> dict:
    """Run logical deduction tests for a model."""
    print(f"\n{'='*60}")
    print(f"🧩 Logical Deduction Tests: {model}")
    print(f"{'='*60}")
    
    results = {"model": model, "passed": 0, "total": len(TESTS), "time": 0, "categories": {}}
    
    # Custom system prompt for logical deduction (ignored if use_mf_sys=True)
    logic_prompt = """Answer logical reasoning questions carefully.

Instructions:
- Think step by step if needed, but give a direct final answer
- For yes/no questions, answer yes or no
- For number questions, give just the number
- Use "uncertain" if the conclusion cannot be determined from the premises
- Be precise and logical"""

    # Note: We create a fresh agent for each test to avoid memory contamination
    
    for test in TESTS:
        category = test["category"]
        prompt = test["prompt"]
        expected = test["expected"]
        check_type = test["type"]
        
        print(f"\n📋 [{category}] {prompt}...")
        
        # Create fresh agent for each test (isolates memory)
        # If use_mf_sys=True, don't pass custom system_prompt (use model's Modelfile)
        agent_kwargs = {
            "model": model,
            "backend": backend,
            "max_steps": 1,
            "debug": debug,
            "soul": soul,
            "soul_level": soul_level,
            "force_react": force_react,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
            "temperature": temperature,
            "top_p": top_p,
        }
        if not use_mf_sys:
            agent_kwargs["system_prompt"] = logic_prompt
        
        agent = Agent(**agent_kwargs)
        
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
    
    print(f"\n⚛️ AgentKthx Logical Deduction Tests ({len(TESTS)} questions)")
    print(f"   Backend: {backend_name} ({backend.base_url})")
    print(f"   Model: {model}")
    if api_mode != 'openre':
        print(f"   API Mode: {api_mode}")
    if timeout:
        print(f"   Timeout: {timeout}s")
    if args.use_modelfile_system:
        print(f"   System Prompt: Modelfile (native)")
    else:
        print(f"   System Prompt: Custom (logical deduction)")
    
    result = run_tests(model, backend, args.debug, args.use_modelfile_system,
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
        print(f"  {category:<18} {bar} {stats['passed']}/{stats['total']} ({pct:.0f}%)")
    
    pass_rate = result["passed"] / result["total"] * 100
    print(f"\n📊 Overall: {result['passed']}/{result['total']} ({pass_rate:.0f}%) in {result['time']:.1f}s")
    print(f"{'='*60}")
    
    result["exit_code"] = 0 if result["passed"] == result["total"] else 1
    return result


if __name__ == "__main__":
    result = main()
    sys.exit(result.get("exit_code", 0))