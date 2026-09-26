#!/usr/bin/env python3
"""
examples/08_reading_comprehension.py
------------------------------------
Reading comprehension tests.

Tests ability to understand and extract information from text:
  • Factual extraction
  • Inference from text
  • Main idea identification
  • Detail questions

Usage:
  python examples/08_reading_comprehension.py
  python examples/08_reading_comprehension.py --model qwen2.5:0.5b --debug
  agentkthx test 08

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
    parser = argparse.ArgumentParser(description="AgentKthx Reading Comprehension Tests")
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


# Reading comprehension tests
TESTS = [
    # Factual extraction
    {
        "category": "Factual",
        "prompt": """Read: "Marie Curie was a physicist and chemist who conducted pioneering research on radioactivity. She was the first woman to win a Nobel Prize and the only person to win Nobel Prizes in two different sciences."
Question: What specific phenomenon did Marie Curie research? Answer with one word.""",
        "expected": "radioactivity",
        "type": "keyword",
    },
    {
        "category": "Factual",
        "prompt": """Read: "The Great Wall of China is over 13,000 miles long and was built over many centuries to protect against invasions."
Question: How long is the Great Wall of China? Answer with a number.""",
        "expected": "13000",
        "type": "number",
    },
    {
        "category": "Factual",
        "prompt": """Read: "Photosynthesis is the process by which plants use sunlight, water, and carbon dioxide to create oxygen and energy in the form of sugar."
Question: What gas do plants produce during photosynthesis? Answer with one word.""",
        "expected": "oxygen",
        "type": "exact",
    },
    {
        "category": "Factual",
        "prompt": """Read: "The Amazon River flows through South America and is approximately 4,000 miles long, making it the second-longest river in the world."
Question: Which continent does the Amazon River flow through? Answer with one word.""",
        "expected": "america",
        "type": "keyword",
    },
    {
        "category": "Factual",
        "prompt": """Read: "The human heart has four chambers: the left and right atria, and the left and right ventricles."
Question: How many chambers does the human heart have? Answer with a number.""",
        "expected": "4",
        "type": "number",
    },
    
    # Inference
    {
        "category": "Inference",
        "prompt": """Read: "Sarah forgot her umbrella at home. When she walked outside, dark clouds filled the sky."
Question: What will likely happen to Sarah when it rains? Answer with one word about her condition.""",
        "expected": "wet",
        "type": "keyword",
    },
    {
        "category": "Inference",
        "prompt": """Read: "The restaurant was completely empty at 8 PM on a Saturday. The chef was sleeping in the kitchen."
Question: Is the restaurant popular? Answer yes or no.""",
        "expected": "no",
        "type": "exact",
    },
    {
        "category": "Inference",
        "prompt": """Read: "Tom studied for three weeks straight. He barely slept the night before. When results came out, he smiled."
Question: Did Tom likely pass his exam? Answer yes or no.""",
        "expected": "yes",
        "type": "exact",
    },
    {
        "category": "Inference",
        "prompt": """Read: "The phone kept ringing but no one answered. The lights were off and the mailbox was overflowing."
Question: Are the residents likely home? Answer yes or no.""",
        "expected": "no",
        "type": "exact",
    },
    {
        "category": "Inference",
        "prompt": """Read: "Despite the heavy rain, the soccer game continued. The players were covered in mud by halftime."
Question: Is this an indoor or outdoor game? Answer with one word.""",
        "expected": "outdoor",
        "type": "exact",
    },
    
    # Main idea
    {
        "category": "Main Idea",
        "prompt": """Read: "Honey never spoils. Archaeologists have found 3000-year-old honey in Egyptian tombs that was still perfectly edible. This is because honey has low moisture and is acidic, making it difficult for bacteria to grow."
Question: What is the main idea? Answer in one sentence.""",
        "expected": "spoil",
        "type": "keyword",
    },
    {
        "category": "Main Idea",
        "prompt": """Read: "Many people think goldfish have a three-second memory, but this is a myth. Scientists have proven that goldfish can remember things for months and can even be trained to perform tricks."
Question: What misconception does this passage address? Answer briefly.""",
        "expected": "memory",
        "type": "keyword",
    },
    {
        "category": "Main Idea",
        "prompt": """Read: "Coffee is one of the most traded commodities in the world. It is grown in over 70 countries, with Brazil being the largest producer. Over 2 billion cups of coffee are consumed daily worldwide."
Question: What is this passage mainly about? Answer with one word.""",
        "expected": "coffee",
        "type": "exact",
    },
    {
        "category": "Main Idea",
        "prompt": """Read: "The printing press, invented by Gutenberg around 1440, revolutionized communication. Before it, books were hand-copied and extremely expensive. The printing press made books affordable and knowledge accessible to many more people."
Question: What effect did the printing press have? Answer briefly.""",
        "expected": "accessible",
        "type": "keyword",
    },
    {
        "category": "Main Idea",
        "prompt": """Read: "Dolphins sleep with one eye open. Half of their brain stays awake to watch for predators and control breathing, while the other half rests. This is called unihemispheric sleep."
Question: How do dolphins sleep differently from humans? Answer briefly.""",
        "expected": "eye",
        "type": "keyword",
    },
    
    # Sequencing
    {
        "category": "Sequencing",
        "prompt": """Read: "First, the chef chopped the vegetables. Then he heated the oil in a pan. After that, he added the vegetables and stirred them for five minutes. Finally, he added the sauce."
Question: What happened immediately after heating the oil? Answer briefly.""",
        "expected": "vegetables",
        "type": "keyword",
    },
    {
        "category": "Sequencing",
        "prompt": """Read: "The seed was planted in spring. By summer, it had grown into a small plant. In fall, flowers appeared. By winter, the plant died back."
Question: In which season did the plant flower? Answer with one word.""",
        "expected": "fall",
        "type": "exact",
    },
    {
        "category": "Sequencing",
        "prompt": """Read: "John woke up at 7 AM. He ate breakfast at 7:30. He left for work at 8:15. He arrived at the office at 9:00."
Question: What did John do between breakfast and leaving for work? Answer with a time-based phrase.""",
        "expected": "left",
        "type": "keyword",
    },
    {
        "category": "Sequencing",
        "prompt": """Read: "The match started at 3 PM. The first goal was scored at 3:25. Halftime was at 4:00. The match ended at 5:00."
Question: What happened first? Answer with one phrase.""",
        "expected": "started",
        "type": "keyword",
    },
    {
        "category": "Sequencing",
        "prompt": """Read: "The order was placed on Monday. It shipped on Wednesday. It arrived on Friday. The customer returned it on Saturday."
Question: How many days between placing the order and receiving it? Answer with a number.""",
        "expected": "4",
        "type": "number",
    },
    
    # Vocabulary in context
    {
        "category": "Vocabulary",
        "prompt": """Read: "The ancient artifact was fragile, so the archaeologists handled it with extreme care."
Question: Does fragile mean delicate or strong? Answer with one word.""",
        "expected": "delicate",
        "type": "keyword",
    },
    {
        "category": "Vocabulary",
        "prompt": """Read: "Despite the arduous journey, the explorers finally reached their destination."
Question: Does arduous mean difficult or easy? Answer with one word.""",
        "expected": "difficult",
        "type": "keyword",
    },
    {
        "category": "Vocabulary",
        "prompt": """Read: "Her meticulous attention to detail made her an excellent proofreader."
Question: Does meticulous mean careful or careless? Answer with one word.""",
        "expected": "careful",
        "type": "exact",
    },
    {
        "category": "Vocabulary",
        "prompt": """Read: "The singer's performance was mediocre at best; the audience was clearly disappointed."
Question: Was the performance excellent or average? Answer with one word.""",
        "expected": "average",
        "type": "keyword",
    },
    {
        "category": "Vocabulary",
        "prompt": """Read: "The abundant harvest meant the farmers would have plenty to sell at market."
Question: Does abundant mean plentiful or scarce? Answer with one word.""",
        "expected": "plentiful",
        "type": "exact",
    },
]


def check_answer(response: str, expected: str, check_type: str) -> bool:
    """Check if response matches expected answer with flexible matching."""
    import re
    response_lower = response.lower().strip()
    expected_lower = expected.lower().strip()
    
    # Synonyms and acceptable alternatives for vocabulary and concepts
    synonyms = {
        # Vocabulary - delicate vs strong
        "delicate": ["delicate", "fragile", "breakable", "easily broken", "careful", "weak"],
        # Vocabulary - difficult vs easy
        "difficult": ["difficult", "hard", "tough", "challenging", "arduous", "strenuous", "demanding"],
        # Vocabulary - careful vs careless
        "careful": ["careful", "meticulous", "thorough", "precise", "diligent", "attentive"],
        # Vocabulary - average vs excellent
        "average": ["average", "mediocre", "ordinary", "moderate", "so-so", "ok", "okay", "not excellent"],
        # Vocabulary - plentiful vs scarce
        "plentiful": ["plentiful", "abundant", "plenty", "ample", "copious", "bountiful", "rich", "lots", "not scarce"],
        # Main idea / concepts
        "spoil": ["spoil", "spoils", "spoiled", "never spoils", "edible", "preserved", "lasts"],
        "memory": ["memory", "remember", "recall", "retention", "myth"],
        "coffee": ["coffee", "caffeine", "beverage", "drink"],
        "accessible": ["accessible", "access", "available", "affordable", "cheaper", "spread"],
        "eye": ["eye", "eyes", "open", "unihemispheric", "one eye", "half brain"],
        # Factual
        "radioactivity": ["radioactivity", "radioactive", "radiation", "nuclear", "physics", "chemistry"],
        "america": ["america", "american", "south america", "south american"],
        # Inference
        "wet": ["wet", "rain", "rained", "soaked", "drenched", "get wet", "umbrella"],
        # Sequencing
        "vegetables": ["vegetable", "vegetables", "added", "chopped"],
        "fall": ["fall", "autumn"],
        "started": ["started", "began", "start", "begin", "3 pm", "3:00"],
        "left": ["left", "leave", "departed", "went", "8:15"],
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
                # Allow tolerance
                if exp_val > 100:
                    return abs(resp_val - exp_val) < exp_val * 0.1  # 10% tolerance for large numbers
                return abs(resp_val - exp_val) < 1.0
            except ValueError:
                return False
        return False
    
    return False


def run_tests(model: str, backend, debug: bool = False, use_mf_sys: bool = False,
              soul: str = None, soul_level: int = 2,
              force_react: bool = False,
              num_ctx: int = None, num_predict: int = None,
              temperature: float = None, top_p: float = None) -> dict:
    """Run reading comprehension tests for a model."""
    print(f"\n{'='*60}")
    print(f"📖 Reading Comprehension Tests: {model}")
    print(f"{'='*60}")
    
    results = {"model": model, "passed": 0, "total": len(TESTS), "time": 0, "categories": {}}
    
    # Custom system prompt for reading comprehension (ignored if use_mf_sys=True)
    comprehension_prompt = """Answer questions based on the text provided. Be direct and concise.

Instructions:
- Read the text carefully
- Answer with the specific information requested
- Use one word when asked for one word
- Use numbers when asked for numbers
- Be brief and accurate"""

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
            agent_kwargs["system_prompt"] = comprehension_prompt
        
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
    
    print(f"\n⚛️ AgentKthx Reading Comprehension Tests ({len(TESTS)} questions)")
    print(f"   Backend: {backend_name} ({backend.base_url})")
    print(f"   Model: {model}")
    if api_mode != 'openre':
        print(f"   API Mode: {api_mode}")
    if timeout:
        print(f"   Timeout: {timeout}s")
    if args.use_modelfile_system:
        print(f"   System Prompt: Modelfile (native)")
    else:
        print(f"   System Prompt: Custom (reading comprehension)")
    
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
        print(f"  {category:<15} {bar} {stats['passed']}/{stats['total']} ({pct:.0f}%)")
    
    pass_rate = result["passed"] / result["total"] * 100
    print(f"\n📊 Overall: {result['passed']}/{result['total']} ({pass_rate:.0f}%) in {result['time']:.1f}s")
    print(f"{'='*60}")
    
    result["exit_code"] = 0 if result["passed"] == result["total"] else 1
    return result


if __name__ == "__main__":
    result = main()
    sys.exit(result.get("exit_code", 0))