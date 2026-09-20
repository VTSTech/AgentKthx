#!/usr/bin/env python3
"""
examples/10_implicit_reasoning.py
---------------------------------
Implicit reasoning tests.

Tests ability to infer unstated information:
  • Understanding implied meanings
  • Drawing unstated conclusions
  • Reading between the lines
  • Recognizing assumptions

Usage:
  python examples/10_implicit_reasoning.py
  python examples/10_implicit_reasoning.py --model qwen2.5:0.5b --debug
  agentkthx test 10

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
    parser = argparse.ArgumentParser(description="AgentKthx Implicit Reasoning Tests")
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


# Implicit reasoning tests (inspired by BIG-bench implicit_states and social_iqa)
TESTS = [
    # Understanding implied states
    {
        "category": "Implied States",
        "prompt": "John looked at his empty wallet and sighed. What is John likely feeling? Answer with one word.",
        "expected": "sad",
        "type": "keyword",
    },
    {
        "category": "Implied States",
        "prompt": "Mary checked her watch for the third time in two minutes. What is Mary likely feeling? Answer with one word.",
        "expected": "impatient",
        "type": "keyword",
    },
    {
        "category": "Implied States",
        "prompt": "The child's eyes lit up when she saw the puppy. How does the child feel? Answer with one word.",
        "expected": "happy",
        "type": "keyword",
    },
    {
        "category": "Implied States",
        "prompt": "Tom put on his coat and grabbed his umbrella before leaving. What does Tom expect? Answer with one word.",
        "expected": "rain",
        "type": "keyword",
    },
    {
        "category": "Implied States",
        "prompt": "She turned up the thermostat and wrapped herself in a blanket. What was the problem? Answer with one word.",
        "expected": "cold",
        "type": "keyword",
    },
    
    # Understanding intentions
    {
        "category": "Intentions",
        "prompt": "A man walks into a bookstore and heads straight to the cookbook section. What does he likely want to do? Answer with one word.",
        "expected": "cook",
        "type": "keyword",
    },
    {
        "category": "Intentions",
        "prompt": "Someone researches flight prices and hotel rates for Paris. What are they planning? Answer with one word.",
        "expected": "travel",
        "type": "keyword",
    },
    {
        "category": "Intentions",
        "prompt": "A student highlights key passages and makes flashcards. What is the student preparing for? Answer with one word.",
        "expected": "exam",
        "type": "keyword",
    },
    {
        "category": "Intentions",
        "prompt": "The woman bought candles, wine, and ingredients for pasta. What is she likely planning? Answer with one word.",
        "expected": "dinner",
        "type": "keyword",
    },
    {
        "category": "Intentions",
        "prompt": "Someone fills a water bottle and puts on running shoes. What activity are they about to do? Answer with one word.",
        "expected": "run",
        "type": "keyword",
    },
    
    # Understanding unstated consequences
    {
        "category": "Consequences",
        "prompt": "The driver didn't see the red light. What likely happened next? Answer with one word.",
        "expected": "accident",
        "type": "keyword",
    },
    {
        "category": "Consequences",
        "prompt": "She stayed up all night studying. How will she likely feel in the morning? Answer with one word.",
        "expected": "tired",
        "type": "keyword",
    },
    {
        "category": "Consequences",
        "prompt": "The company laid off half its employees. What will likely happen to the remaining employees' workload? Answer with one word.",
        "expected": "increase",
        "type": "keyword",
    },
    {
        "category": "Consequences",
        "prompt": "He skipped breakfast and lunch. What will he likely feel by dinner time? Answer with one word.",
        "expected": "hungry",
        "type": "keyword",
    },
    {
        "category": "Consequences",
        "prompt": "The team practiced every day for months. What is likely true about their performance? Answer with one word.",
        "expected": "improved",
        "type": "keyword",
    },
    
    # Understanding social situations
    {
        "category": "Social",
        "prompt": "At the party, Lisa stood in the corner checking her phone while others danced. How would you describe Lisa at the party? Answer with one word.",
        "expected": "bored",
        "type": "keyword",
    },
    {
        "category": "Social",
        "prompt": "The boss called an unexpected meeting and looked serious. How do the employees likely feel? Answer with one word.",
        "expected": "nervous",
        "type": "keyword",
    },
    {
        "category": "Social",
        "prompt": "Two friends haven't spoken in weeks after their argument. What is their current relationship status? Answer with one word.",
        "expected": "strained",
        "type": "keyword",
    },
    {
        "category": "Social",
        "prompt": "The child hid behind his mother when meeting the new neighbor. What emotion is the child showing? Answer with one word.",
        "expected": "shy",
        "type": "keyword",
    },
    {
        "category": "Social",
        "prompt": "Everyone at the table laughed when John told his story. What kind of story was it likely? Answer with one word.",
        "expected": "funny",
        "type": "keyword",
    },
    
    # Understanding assumptions
    {
        "category": "Assumptions",
        "prompt": "'Can you pass the salt?' assumes the salt is within what? Answer with one word.",
        "expected": "reach",
        "type": "keyword",
    },
    {
        "category": "Assumptions",
        "prompt": "'I need to charge my phone' assumes the battery is what? Answer with one word.",
        "expected": "low",
        "type": "keyword",
    },
    {
        "category": "Assumptions",
        "prompt": "'Let's meet for lunch' assumes both people are doing what at that time? Answer with one word.",
        "expected": "free",
        "type": "keyword",
    },
    {
        "category": "Assumptions",
        "prompt": "'I'll take the stairs' implies the elevator is what? Answer with one word.",
        "expected": "unavailable",
        "type": "keyword",
    },
    {
        "category": "Assumptions",
        "prompt": "'Did you remember to lock the door?' suggests the speaker thinks there's a possibility the door is what? Answer with one word.",
        "expected": "unlocked",
        "type": "keyword",
    },
]


def check_answer(response: str, expected: str, check_type: str) -> bool:
    """Check if response matches expected answer."""
    response_lower = response.lower()
    expected_lower = expected.lower()
    
    # Synonyms and related words for implicit reasoning
    synonyms = {
        "sad": ["sad", "upset", "worried", "frustrated", "disappointed", "unhappy"],
        "impatient": ["impatient", "anxious", "hurried", "restless", "waiting", "bored"],
        "happy": ["happy", "excited", "delighted", "joyful", "pleased", "thrilled"],
        "rain": ["rain", "storm", "weather", "wet", "precipitation"],
        "cold": ["cold", "chilly", "freezing", "cool", "low temperature"],
        "cook": ["cook", "cooking", "recipe", "food", "prepare"],
        "travel": ["travel", "trip", "vacation", "visit", "journey"],
        "exam": ["exam", "test", "study", "quiz", "assessment"],
        "dinner": ["dinner", "meal", "romantic", "date", "evening"],
        "run": ["run", "jog", "exercise", "workout", "running"],
        "accident": ["accident", "crash", "collision", "wreck"],
        "tired": ["tired", "exhausted", "sleepy", "fatigue", "weary"],
        "increase": ["increase", "more", "higher", "greater", "rise"],
        "hungry": ["hungry", "starving", "famished", "empty"],
        "improved": ["improved", "better", "enhanced", "stronger", "good"],
        "bored": ["bored", "uninterested", "disengaged", "lonely", "awkward"],
        "nervous": ["nervous", "anxious", "worried", "concerned", "scared"],
        "strained": ["strained", "tense", "difficult", "bad", "damaged"],
        "shy": ["shy", "nervous", "scared", "timid", "anxious"],
        "funny": ["funny", "humorous", "amusing", "joke", "comedy"],
        "reach": ["reach", "near", "close", "accessible"],
        "low": ["low", "dead", "empty", "dying"],
        "free": ["free", "available", "open", "not busy"],
        "unavailable": ["unavailable", "broken", "slow", "busy", "not working"],
        "unlocked": ["unlocked", "open", "not locked"],
    }
    
    if check_type == "exact":
        return expected_lower in response_lower
    
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
    """Run implicit reasoning tests for a model."""
    print(f"\n{'='*60}")
    print(f"🤔 Implicit Reasoning Tests: {model}")
    print(f"{'='*60}")
    
    results = {"model": model, "passed": 0, "total": len(TESTS), "time": 0, "categories": {}}
    
    # Custom system prompt for implicit reasoning
    implicit_prompt = """Answer questions by reading between the lines.

Instructions:
- Think about what is implied, not just what is stated
- Consider emotions, intentions, and unstated meanings
- Use one word when asked for one word
- Give the most likely interpretation"""

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
            system_prompt=implicit_prompt,
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
    
    print(f"\n⚛️ AgentKthx Implicit Reasoning Tests ({len(TESTS)} questions)")
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