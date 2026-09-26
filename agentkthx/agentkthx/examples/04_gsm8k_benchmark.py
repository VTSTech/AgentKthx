#!/usr/bin/env python3
"""
examples/04_gsm8k_benchmark.py
------------------------------
GSM8K-style math benchmark using AgentKthx Agent System.

Features:
  • Calculator tool for accurate arithmetic
  • 50 math questions (basic to intermediate)
  • Multi-model comparison support
  • Results saved to JSONL

Usage:
  python examples/04_gsm8k_benchmark.py
  python examples/04_gsm8k_benchmark.py --model qwen2.5:0.5b
  python examples/04_gsm8k_benchmark.py --models all
  agentkthx test 04

Environment Variables:
  OLLAMA_BASE_URL     - Ollama server URL
  AGENTKTHX_MODEL     - Default model

Written by VTSTech — https://www.vts-tech.org
"""

import sys
import os
import time
import json
import re
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentkthx import Agent, get_config
from agentkthx.backends import get_default_backend, OllamaBackend
from agentkthx.tools import make_builtin_registry


def parse_args():
    parser = argparse.ArgumentParser(description="GSM8K Agent Benchmark")
    parser.add_argument("-m", "--model", default=None, help="Model to test")
    parser.add_argument("--models", choices=["all"], help="Test all available models")
    parser.add_argument("--limit", type=int, default=50, help="Number of questions")
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


# GSM8K-style questions (50 questions)
QUESTIONS = [
    # Basic arithmetic (1-10)
    ("What is 15 + 27?", "42"),
    ("What is 8 times 7?", "56"),
    ("What is 100 minus 37?", "63"),
    ("What is 144 divided by 12?", "12"),
    ("What is 25 times 4?", "100"),
    ("What is 17 + 18 + 19?", "54"),
    ("What is 200 - 50 - 30?", "120"),
    ("What is 6 times 9?", "54"),
    ("What is 81 divided by 9?", "9"),
    ("What is 35 + 65?", "100"),
    
    # Word problems basic (11-20)
    ("Janet has 8 apples. She buys 12 more. How many apples does she have now?", "20"),
    ("A book costs $15. If you pay with $20, how much change do you get?", "5"),
    ("There are 24 students in a class. If 6 are absent, how many are present?", "18"),
    ("A pizza has 8 slices. If 3 people share it equally, how many slices each?", "3"),
    ("Tom has 45 marbles. He gives 18 to his friend. How many does he have left?", "27"),
    ("A train travels 60 miles per hour. How far does it travel in 2 hours?", "120"),
    ("What is 999 minus 777?", "222"),
    ("If 5 pens cost $10, how much does 1 pen cost?", "2"),
    ("What is 15% of 80?", "12"),
    ("A rectangle is 8 feet long and 5 feet wide. What is the area in square feet?", "40"),
    
    # Intermediate arithmetic (21-30)
    ("What is 12 times 11?", "132"),
    ("What is 3 squared plus 4 squared?", "25"),
    ("A store has 156 items. They sell 89. How many remain?", "67"),
    ("What is half of 150?", "75"),
    ("What is 7 times 8?", "56"),
    ("What is 225 divided by 15?", "15"),
    ("What is 33 + 44 + 55?", "132"),
    ("What is 1000 minus 777?", "223"),
    ("What is 9 times 8?", "72"),
    ("What is 18 divided by 3?", "6"),
    
    # Word problems intermediate (31-40)
    ("Mary reads 12 pages per day. How many pages in 5 days?", "60"),
    ("A box contains 48 eggs. If 12 eggs are broken, how many are good?", "36"),
    ("John earns $15 per hour. How much for 6 hours of work?", "90"),
    ("A cake has 12 slices. If 4 friends share equally, how many slices each?", "3"),
    ("What is 20% of 200?", "40"),
    ("A car travels 50 miles on 2 gallons. How many miles per gallon?", "25"),
    ("A dozen eggs costs $3. How much for 4 dozen?", "12"),
    ("What is 50 minus 17?", "33"),
    ("A garden has 15 rows with 8 plants each. How many plants total?", "120"),
    ("What is 11 times 11?", "121"),
    
    # Advanced (41-50)
    ("What is 16 times 5?", "80"),
    ("A shirt costs $25. If it is 20% off, how much do you save?", "5"),
    ("What is 360 divided by 6?", "60"),
    ("A pool holds 500 gallons. 125 gallons evaporate. How many remain?", "375"),
    ("What is 14 plus 28 plus 42?", "84"),
    ("A recipe needs 3 cups of flour for 12 cookies. How many cups for 24 cookies?", "6"),
    ("What is 45 minus 19?", "26"),
    ("A train has 8 cars with 15 passengers each. How many passengers total?", "120"),
    ("What is 7 times 9?", "63"),
    ("A store opens at 9am and closes at 5pm. How many hours is it open?", "8"),
]


def extract_number(text: str) -> str:
    """Extract first number from text."""
    match = re.search(r'-?\d+\.?\d*', text.replace(',', ''))
    return match.group(0) if match else ""


def run_benchmark(model: str, backend, questions: list, debug: bool = False,
                   soul: str = None, soul_level: int = 2,
                   force_react: bool = False,
                   num_ctx: int = None, num_predict: int = None,
                   temperature: float = None, top_p: float = None) -> dict:
    """Run benchmark for a single model."""
    print(f"\n{'='*60}")
    print(f"🧮 GSM8K Benchmark: {model}")
    print(f"{'='*60}")
    
    tools = make_builtin_registry().subset(["calculator"])
    results = {"model": model, "passed": 0, "total": len(questions), "time": 0, "details": []}
    
    agent = Agent(
        model=model,
        tools=tools,
        backend=backend,
        max_steps=5,
        debug=debug,
        soul=soul,
        soul_level=soul_level,
        force_react=force_react,
        num_ctx=num_ctx,
        num_predict=num_predict,
        temperature=temperature,
        top_p=top_p,
    )
    
    correct = 0
    for i, (question, expected) in enumerate(questions):
        t0 = time.time()
        run = agent.run(question)
        elapsed = time.time() - t0
        results["time"] += elapsed
        
        response = run.final_answer
        extracted = extract_number(response)
        
        # Check correctness
        try:
            is_correct = abs(float(extracted) - float(expected)) < 0.01
        except (ValueError, TypeError):
            is_correct = extracted == expected
        
        if is_correct:
            correct += 1
        
        status = "✓" if is_correct else "✗"
        print(f"  Q{i+1:2d}: {status} Expected: {expected}, Got: {extracted} ({elapsed:.1f}s)")
        
        results["details"].append({
            "question": question,
            "expected": expected,
            "response": response,
            "extracted": extracted,
            "correct": is_correct,
            "time": elapsed,
        })
    
    results["passed"] = correct
    accuracy = correct / len(questions) * 100
    print(f"\n📊 Score: {correct}/{len(questions)} = {accuracy:.1f}% in {results['time']:.1f}s")
    
    return results


def main():
    args = parse_args()
    config = get_config()
    
    backend_name = args.backend or config.backend
    api_mode = getattr(args, 'api_mode', 'openre')
    timeout = getattr(args, 'timeout', None)
    backend = get_default_backend(backend_name, api_mode=api_mode, timeout=timeout)
    
    if not backend.is_running():
        print(f"❌ {backend_name.capitalize()} not running at {backend.base_url}")
        return 1
    
    print(f"   API Mode: {api_mode}")
    if timeout:
        print(f"   Timeout: {timeout}s")
    
    # Determine models to test
    if args.models == "all" and isinstance(backend, OllamaBackend):
        models_data = backend.list_models()
        models = [m["name"] for m in models_data if "embed" not in m["name"].lower()]
    else:
        models = [args.model or config.default_model]
    
    questions = QUESTIONS[:args.limit]
    
    print(f"\n⚛️ GSM8K Agent Benchmark")
    print(f"   Backend: {backend_name} ({backend.base_url})")
    print(f"   Models: {len(models)}")
    print(f"   Questions: {len(questions)}")
    print(f"{'='*60}")
    
    all_results = []
    for model in models:
        result = run_benchmark(model, backend, questions, args.debug,
                              soul=args.soul, soul_level=args.soul_level,
                              force_react=getattr(args, 'force_react', False),
                              num_ctx=getattr(args, 'num_ctx', None),
                              num_predict=getattr(args, 'num_predict', None),
                              temperature=getattr(args, 'temperature', None),
                              top_p=getattr(args, 'top_p', None))
        all_results.append(result)
    
    # Summary for multiple models
    if len(all_results) > 1:
        print(f"\n{'='*60}")
        print("🏆 RANKINGS")
        print(f"{'='*60}")
        
        sorted_results = sorted(all_results, key=lambda x: (-x["passed"], x["time"]))
        for i, r in enumerate(sorted_results, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "  "
            accuracy = r["passed"] / r["total"] * 100
            print(f"{medal} {r['model']:<35} {r['passed']}/{r['total']} ({accuracy:.0f}%) - {r['time']:.1f}s")
    
    # Save results
    results_file = "gsm8k_results.jsonl"
    with open(results_file, "w") as f:
        for result in all_results:
            f.write(json.dumps(result) + "\n")
    print(f"\n💾 Results saved to: {results_file}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())