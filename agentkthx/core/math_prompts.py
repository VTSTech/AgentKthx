"""
⚛️ AgentKthx R02 — Math Prompts and Utilities

Math-specific prompts and utilities for GSM8K-style benchmarks:
  • System prompts optimized for math reasoning
  • Compact prompts for small models
  • Number extraction from model output
  • Calculator tool function

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import ast
import math
import re
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# MATH SYSTEM PROMPTS
# ═══════════════════════════════════════════════════════════════════════════════

MATH_SYSTEM_PROMPT = """You are a precise math assistant. Solve problems step by step.

IMPORTANT RULES:
1. Use the calculator tool for ALL arithmetic operations
2. Show your reasoning step by step
3. Give ONLY the final number as your answer on the last line
4. Do not include units or explanations in the final answer

Examples:

User: What is 15 + 27?
Assistant: Let me calculate 15 + 27.
[uses calculator tool]
Result: 42
Answer: 42

User: Janet has 8 apples. She buys 12 more. How many?
Assistant: Janet starts with 8 apples and buys 12 more. I need to add them.
[uses calculator tool]
Result: 20
Answer: 20

User: What is 144 divided by 12?
Assistant: I need to divide 144 by 12.
[uses calculator tool]
Result: 12
Answer: 12

User: A train travels 60 miles per hour. How far in 2 hours?
Assistant: Distance = speed × time. So 60 × 2.
[uses calculator tool]
Result: 120
Answer: 120

Remember: Always use the calculator tool for arithmetic, then give just the number as your final answer."""


MATH_SYSTEM_PROMPT_COMPACT = """You are a math assistant. Use the calculator tool for all arithmetic. Give ONLY the final number as your answer.

Steps:
1. Read the problem
2. Use calculator tool for arithmetic
3. Answer with just the number

Example: "What is 15 + 27?" → use calculator → Answer: 42"""


# For models that DON'T support tools - pure reasoning
MATH_SYSTEM_PROMPT_NO_TOOLS = """You are a math assistant. Solve problems step by step using your reasoning.

IMPORTANT RULES:
1. Think through each problem carefully
2. Show your work step by step
3. Give ONLY the final number as your answer on the last line
4. Do not include units or explanations in the final answer

Examples:

User: What is 15 + 27?
Assistant: I need to add 15 and 27.
15 + 27 = 42
Answer: 42

User: Janet has 8 apples. She buys 12 more. How many?
Assistant: Janet starts with 8 apples and buys 12 more.
8 + 12 = 20
Answer: 20

User: What is 144 divided by 12?
Assistant: I need to divide 144 by 12.
144 / 12 = 12
Answer: 12

Remember: Show your work, then give just the number as your final answer."""


# ReAct-specific prompt that shows the EXACT format needed
MATH_SYSTEM_PROMPT_REACT = """You are a math assistant. You MUST use the calculator tool for ALL arithmetic.

When you need to calculate something, follow this EXACT format:

Thought: I need to [what calculation]
Action: calculator
Action Input: {"expression": "[the math expression]"}
Observation: [result will appear here]
Final Answer: [just the number]

IMPORTANT EXAMPLES:

Question: What is 15 + 27?
Thought: I need to add 15 and 27
Action: calculator
Action Input: {"expression": "15 + 27"}
Observation: 42
Final Answer: 42

Question: What is 8 times 7?
Thought: I need to multiply 8 by 7
Action: calculator
Action Input: {"expression": "8 * 7"}
Observation: 56
Final Answer: 56

Question: What is 144 divided by 12?
Thought: I need to divide 144 by 12
Action: calculator
Action Input: {"expression": "144 / 12"}
Observation: 12
Final Answer: 12

Question: What is 200 minus 50 minus 30?
Thought: I need to subtract 50 and then 30 from 200
Action: calculator
Action Input: {"expression": "200 - 50 - 30"}
Observation: 120
Final Answer: 120

Question: Janet has 8 apples. She buys 12 more. How many?
Thought: Janet starts with 8 apples and buys 12 more, so I add them
Action: calculator
Action Input: {"expression": "8 + 12"}
Observation: 20
Final Answer: 20

RULES:
- "times" means multiply (*)
- "divided by" means divide (/)
- "minus" means subtract (-)
- "plus" means add (+)
- Always give Final Answer as just the number, no words"""


# ═══════════════════════════════════════════════════════════════════════════════
# FEW-SHOT EXAMPLES FOR SMALL MODELS
# ═══════════════════════════════════════════════════════════════════════════════

MATH_FEW_SHOT = [
    {"role": "user", "content": "What is 15 + 27?"},
    {"role": "assistant", "content": "I'll use the calculator.\n calculator(expression='15 + 27')\nResult: 42\nAnswer: 42"},
    {"role": "user", "content": "What is 8 times 7?"},
    {"role": "assistant", "content": "I'll use the calculator.\n calculator(expression='8 * 7')\nResult: 56\nAnswer: 56"},
    {"role": "user", "content": "Tom has 45 marbles. He gives 18 to his friend. How many left?"},
    {"role": "assistant", "content": "I need to subtract: 45 - 18.\n calculator(expression='45 - 18')\nResult: 27\nAnswer: 27"},
]


# ═══════════════════════════════════════════════════════════════════════════════
# CALCULATOR TOOL FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def calculator_tool(expression: str) -> str:
    """
    Safe math evaluator for use as a tool function.
    
    Evaluates mathematical expressions using Python's math module.
    Supports: +, -, *, /, **, sqrt, log, sin, cos, tan, etc.
    
    Parameters
    ----------
    expression : str
        Mathematical expression to evaluate (e.g., "2 ** 10", "sqrt(144)")
    
    Returns
    -------
    str
        The result as a string, or an error message
    """
    # Allowed names from math module
    allowed_names = {k: v for k, v in math.__dict__.items() if not k.startswith("_")}
    allowed_names.update({
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
    })
    
    try:
        # Parse the expression
        tree = ast.parse(expression, mode="eval")
        
        # Walk the tree and check for disallowed operations
        for node in ast.walk(tree):
            # Block imports
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                return "[Error] Imports not allowed"
            
            # Check function calls
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                    if func_name not in allowed_names:
                        return f"[Error] Function '{func_name}' not allowed"
        
        # Evaluate safely
        result = eval(
            compile(tree, "<calculator>", "eval"),
            {"__builtins__": {}},
            allowed_names
        )
        
        # Format result
        if isinstance(result, float):
            # Clean up float display
            if result == int(result):
                return str(int(result))
            return str(round(result, 10))
        
        return str(result)
        
    except SyntaxError as e:
        return f"[Syntax error] {e}"
    except NameError as e:
        return f"[Name error] Unknown function or variable"
    except ZeroDivisionError:
        return "[Error] Division by zero"
    except OverflowError:
        return "[Error] Result too large"
    except Exception as e:
        return f"[Calculator error] {type(e).__name__}: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# NUMBER EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

def extract_number(text: str) -> str:
    """
    Extract the final answer number from model output.
    
    Tries multiple strategies:
    1. Look for explicit "Answer:" or "Result:" patterns
    2. Look for equals sign at end
    3. Take the last number in the text
    
    Parameters
    ----------
    text : str
        Model output text to parse
    
    Returns
    -------
    str
        Extracted number as string, or empty string if not found
    """
    if not text:
        return ""
    
    text_str = str(text).strip()
    
    # Strategy 1: Look for explicit answer patterns
    patterns = [
        r'(?:final answer|answer|result)[:\s]*([+-]?\d+\.?\d*)',
        r'answer[:\s]+(?:is\s+)?([+-]?\d+\.?\d*)',
        r'=\s*([+-]?\d+\.?\d*)\s*$',
        r'(?:the answer is|therefore|so|thus)[,\s]+([+-]?\d+\.?\d*)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text_str, re.IGNORECASE | re.MULTILINE)
        if match:
            num = match.group(1)
            # Clean up
            num = num.rstrip('.')
            if num.startswith('+'):
                num = num[1:]
            return num
    
    # Strategy 2: Find all numbers, take the last one
    # Match integers and decimals, including negative
    numbers = re.findall(r'[+-]?\d+\.?\d*', text_str)
    
    if numbers:
        # Filter out likely non-answer numbers (like equation coefficients)
        # Take the last number that appears after calculation keywords
        calc_keywords = ['=', 'result', 'answer', 'total', 'sum', 'is']
        
        text_lower = text_str.lower()
        for keyword in reversed(calc_keywords):
            if keyword in text_lower:
                # Find numbers after this keyword
                idx = text_lower.rfind(keyword)
                after = text_str[idx:]
                nums_after = re.findall(r'[+-]?\d+\.?\d*', after)
                if nums_after:
                    num = nums_after[-1].rstrip('.').lstrip('+')
                    return num
        
        # Fallback: just take last number
        num = numbers[-1].rstrip('.').lstrip('+')
        return num
    
    return ""


def extract_and_normalize(text: str) -> float | None:
    """
    Extract number and convert to float.
    
    Returns None if no number found or conversion fails.
    """
    extracted = extract_number(text)
    if extracted:
        try:
            return float(extracted)
        except ValueError:
            return None
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def is_correct_answer(extracted: str, expected: str, tolerance: float = 0.01) -> bool:
    """
    Check if extracted answer matches expected within tolerance.
    
    Parameters
    ----------
    extracted : str
        Extracted answer from model
    expected : str
        Expected answer
    tolerance : float
        Acceptable difference for floating point comparison
    
    Returns
    -------
    bool
        True if answers match
    """
    if not extracted or not expected:
        return False
    
    # Try exact string match first
    if extracted.strip() == expected.strip():
        return True
    
    # Try numeric comparison
    try:
        ext_val = float(extracted)
        exp_val = float(expected)
        return abs(ext_val - exp_val) <= tolerance
    except ValueError:
        return False


def format_math_prompt(question: str, few_shot: bool = False) -> str:
    """
    Format a math question with appropriate prompting.
    
    Parameters
    ----------
    question : str
        The math question
    few_shot : bool
        Whether to include few-shot examples (for small models)
    
    Returns
    -------
    str
        Formatted prompt
    """
    if few_shot:
        examples = "\n\n".join(
            f"Q: {ex['content']}\nA: {ex['content']}" 
            for ex in MATH_FEW_SHOT[::2]
        )
        return f"{examples}\n\nQ: {question}\nA:"
    else:
        return question
