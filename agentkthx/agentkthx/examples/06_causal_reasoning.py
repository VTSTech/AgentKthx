#!/usr/bin/env python3
"""
examples/06_causal_reasoning.py
-------------------------------
Causal reasoning tests (from BIG-bench).

Tests understanding of cause and effect:
  • Identifying causes and effects
  • Distinguishing correlation from causation
  • Understanding causal chains

Usage:
  python examples/06_causal_reasoning.py
  python examples/06_causal_reasoning.py --model qwen2.5:0.5b --debug
  agentkthx test 06

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
    parser = argparse.ArgumentParser(description="AgentKthx Causal Reasoning Tests")
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


# Causal reasoning tests (inspired by BIG-bench causal_judgment)
TESTS = [
    # Direct cause and effect
    {
        "category": "Direct Causal",
        "prompt": "If it rained last night and the ground is wet, what caused the ground to be wet? Answer with one word.",
        "expected": "rain",
        "type": "keyword",
    },
    {
        "category": "Direct Causal",
        "prompt": "A glass fell off a table and broke. What caused the glass to break? Answer in a short phrase.",
        "expected": "falling",
        "type": "keyword",
    },
    {
        "category": "Direct Causal",
        "prompt": "The sun rises in the morning and the birds start singing. What causes the birds to sing? Answer with one factor.",
        "expected": "sunlight",
        "type": "keyword",
    },
    {
        "category": "Direct Causal",
        "prompt": "A plant was watered regularly and grew tall. What caused the plant to grow? Answer with one word.",
        "expected": "water",
        "type": "keyword",
    },
    {
        "category": "Direct Causal",
        "prompt": "Ice was left in the sun and melted. What caused the ice to melt? Answer with one word.",
        "expected": "heat",
        "type": "keyword",
    },
    
    # Distinguishing cause from effect
    {
        "category": "Cause vs Effect",
        "prompt": "A person exercised and their heart rate increased. Which is the cause and which is the effect? Answer: X is cause, Y is effect.",
        "expected": "exercise is cause",
        "type": "keyword",
    },
    {
        "category": "Cause vs Effect",
        "prompt": "A car ran out of fuel and stopped moving. What is the effect in this situation? Answer with one word.",
        "expected": "stopped",
        "type": "keyword",
    },
    {
        "category": "Cause vs Effect",
        "prompt": "A student studied hard and passed the exam. Which came first - studying or passing? Answer with one word.",
        "expected": "studying",
        "type": "keyword",
    },
    {
        "category": "Cause vs Effect",
        "prompt": "The alarm rang and the person woke up. What was the cause of waking up? Answer with one word.",
        "expected": "alarm",
        "type": "keyword",
    },
    {
        "category": "Cause vs Effect",
        "prompt": "The lights went out and the room became dark. What was the effect? Answer with one word.",
        "expected": "dark",
        "type": "keyword",
    },
    
    # Correlation vs Causation
    {
        "category": "Correlation",
        "prompt": "Ice cream sales increase in summer. Drowning incidents also increase in summer. Does eating ice cream cause drowning? Answer yes or no.",
        "expected": "no",
        "type": "exact",
    },
    {
        "category": "Correlation",
        "prompt": "People who carry umbrellas often get wet. Do umbrellas cause people to get wet? Answer yes or no.",
        "expected": "no",
        "type": "exact",
    },
    {
        "category": "Correlation",
        "prompt": "Schools with more libraries tend to have better test scores. Is this because libraries improve learning, or because well-funded schools have both? Answer with the second option's main idea.",
        "expected": "funding",
        "type": "keyword",
    },
    {
        "category": "Correlation",
        "prompt": "Sleeping with shoes on correlates with waking up with a headache. Is the headache caused by shoes? Answer yes or no.",
        "expected": "no",
        "type": "exact",
    },
    {
        "category": "Correlation",
        "prompt": "Nations with more chocolate consumption have more Nobel laureates. Does chocolate cause Nobel prizes? Answer yes or no.",
        "expected": "no",
        "type": "exact",
    },
    
    # Causal chains
    {
        "category": "Causal Chains",
        "prompt": "Pressing a switch turns on a light. What makes the light turn on? Answer with the most direct cause.",
        "expected": "electricity",
        "type": "keyword",
    },
    {
        "category": "Causal Chains",
        "prompt": "A seed is planted, watered, and sprouts. Which action is necessary for sprouting? Answer with one word.",
        "expected": "water",
        "type": "keyword",
    },
    {
        "category": "Causal Chains",
        "prompt": "Eating spoiled food can cause stomach illness. What is the intermediate step? Answer with one word.",
        "expected": "bacteria",
        "type": "keyword",
    },
    {
        "category": "Causal Chains",
        "prompt": "A match is struck, it ignites, and starts a fire. What is the first cause in this chain? Answer with one word.",
        "expected": "friction",
        "type": "keyword",
    },
    {
        "category": "Causal Chains",
        "prompt": "Turning a key starts a car. What happens between turning the key and the engine starting? Answer briefly.",
        "expected": "ignition",
        "type": "keyword",
    },
    
    # Counterfactual reasoning
    {
        "category": "Counterfactual",
        "prompt": "If the sun did not rise tomorrow, what would happen to plants? Answer briefly.",
        "expected": "die",
        "type": "keyword",
    },
    {
        "category": "Counterfactual",
        "prompt": "If humans had no lungs, what would they be unable to do? Answer with one word.",
        "expected": "breathe",
        "type": "keyword",
    },
    {
        "category": "Counterfactual",
        "prompt": "If gravity stopped working, what would happen to objects on Earth? Answer briefly.",
        "expected": "float",
        "type": "keyword",
    },
    {
        "category": "Counterfactual",
        "prompt": "If water did not freeze at 0 degrees Celsius, what would happen to lakes in winter? Answer briefly.",
        "expected": "freeze",
        "type": "keyword",
    },
    {
        "category": "Counterfactual",
        "prompt": "If there were no bees, what would happen to many flowers? Answer briefly.",
        "expected": "pollinate",
        "type": "keyword",
    },
]


def normalize_answer(text: str) -> str:
    """Normalize answer for comparison."""
    return text.lower().strip()


def check_answer(response: str, expected: str, check_type: str) -> bool:
    """Check if response matches expected answer with flexible matching."""
    response_lower = response.lower().strip()
    expected_lower = expected.lower().strip()
    
    # Synonyms and acceptable alternatives for causal reasoning
    synonyms = {
        # Direct causes
        "rain": ["rain", "rained", "rainfall", "precipitation", "water from sky"],
        "falling": ["fall", "falling", "fell", "drop", "dropped"],
        "sunlight": ["sunlight", "sun", "light", "morning", "dawn"],
        "water": ["water", "h2o", "liquid", "hydration", "irrigation"],
        "heat": ["heat", "hot", "warm", "temperature", "sun", "melt"],
        # Cause vs effect
        "stopped": ["stopped", "stop", "halt", "ceased", "ended", "didn't move"],
        "studying": ["studying", "study", "studied", "learn", "learned", "preparation"],
        "alarm": ["alarm", "clock", "wake", "ringing", "sound"],
        "dark": ["dark", "darkness", "black", "no light"],
        # Causal chains
        "electricity": ["electricity", "electric", "power", "current", "electrical"],
        "bacteria": ["bacteria", "germs", "microorganism", "infection", "food poisoning"],
        "friction": ["friction", "strike", "scratched", "heat", "spark"],
        "ignition": ["ignition", "ignite", "spark", "start", "fire", "combustion"],
        # Counterfactual
        "die": ["die", "death", "dying", "perish", "wilt", "not survive"],
        "breathe": ["breathe", "breathing", "breath", "respiration", "air"],
        "float": ["float", "floating", "fly", "drift", "weightless", "up"],
        "freeze": ["freeze", "frozen", "ice", "solid", "solidify"],
        "pollinate": ["pollinate", "pollination", "reproduce", "flowers", "seeds"],
        # Funding/correlation
        "funding": ["funding", "fund", "money", "wealth", "resource", "budget"],
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
    
    return False


def run_tests(model: str, backend, debug: bool = False,
              soul: str = None, soul_level: int = 2,
              force_react: bool = False,
              num_ctx: int = None, num_predict: int = None,
              temperature: float = None, top_p: float = None) -> dict:
    """Run causal reasoning tests for a model."""
    print(f"\n{'='*60}")
    print(f"🔗 Causal Reasoning Tests: {model}")
    print(f"{'='*60}")
    
    results = {"model": model, "passed": 0, "total": len(TESTS), "time": 0, "categories": {}}
    
    # Custom system prompt for causal reasoning
    causal_prompt = """Answer questions about cause and effect relationships.

Instructions:
- Think about what causes what
- Distinguish between correlation and causation
- Use one word when asked for one word
- Be direct and logical"""

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
            max_steps=1,  # Single-step for reasoning (no tools)
            debug=debug,
            system_prompt=causal_prompt,
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
    
    print(f"\n⚛️ AgentKthx Causal Reasoning Tests ({len(TESTS)} questions)")
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
        print(f"  {category:<18} {bar} {stats['passed']}/{stats['total']} ({pct:.0f}%)")
    
    pass_rate = result["passed"] / result["total"] * 100
    print(f"\n📊 Overall: {result['passed']}/{result['total']} ({pass_rate:.0f}%) in {result['time']:.1f}s")
    print(f"{'='*60}")
    
    result["exit_code"] = 0 if result["passed"] == result["total"] else 1
    return result


if __name__ == "__main__":
    result = main()
    sys.exit(result.get("exit_code", 0))