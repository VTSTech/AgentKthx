"""
⚛️ AgentKthx — Argument Normalizer
Functions for normalizing and fixing tool call arguments from small models.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import re


def strip_tool_prefix(result: str) -> str:
    """Strip the 'tool_name → ' prefix added to successful results entries."""
    return result.split("→")[-1].strip() if "→" in result else result.strip()


def normalize_args(args: dict, tool, tool_name: str = None) -> dict:
    """
    Small models often hallucinate argument keys. This function normalizes
    them using multiple strategies:
    
    1. Tool-specific alias mapping (TOOL_ARG_ALIASES)
    2. Exact match to real params
    3. Prefix/substring matching
    4. Type coercion (string -> int/float)
    
    Also handles special cases like power operations where multiple args
    (base, exponent) need to be combined into a single expression.
    """
    from .prompts import TOOL_ARG_ALIASES
    
    # Guard: ensure args is a dict
    if not isinstance(args, dict):
        if args is None:
            return {}
        if isinstance(args, str):
            return {"input": args}
        return {}
    
    if tool is None:
        return args

    real_params = [p for p in tool.params]
    if not real_params:
        return args

    param_map = {p.name: p for p in real_params}
    normalized = {}
    power_parts = {}
    
    tool_aliases = TOOL_ARG_ALIASES.get(tool_name, {}) if tool_name else {}
    
    for key, val in args.items():
        key_lower = key.lower().replace("-", "_")
        target_param = None
        target_pname = None
        
        # Strategy 1: Tool-specific alias lookup
        if key_lower in tool_aliases:
            alias_target = tool_aliases[key_lower]
            if alias_target == "_combine_power":
                power_parts[key_lower] = val
                continue
            elif alias_target in param_map:
                target_param = param_map[alias_target]
                target_pname = alias_target
        
        # Strategy 2: Exact match
        if target_param is None and key in param_map:
            target_param = param_map[key]
            target_pname = key
        
        # Strategy 3: Prefix/substring matching
        if target_param is None:
            for p in real_params:
                if p.name in key_lower or key_lower.startswith(p.name):
                    target_param = p
                    target_pname = p.name
                    break
        
        if target_pname is None:
            target_pname = key
        
        # Coerce string numbers to the declared type
        if target_param and isinstance(val, str):
            if target_param.type in ("number", "float"):
                try:
                    val = float(val)
                except ValueError:
                    pass
            elif target_param.type == "integer":
                try:
                    val = int(val)
                except ValueError:
                    pass
        
        if target_pname not in normalized:
            normalized[target_pname] = val
        elif target_pname in normalized and isinstance(normalized[target_pname], str):
            pass
    
    # Handle power operation combination
    if power_parts and "expression" in param_map:
        base = power_parts.get("base") or power_parts.get("value") or power_parts.get("x")
        exp = power_parts.get("exponent") or power_parts.get("power") or power_parts.get("n") or power_parts.get("p") or power_parts.get("exp")
        
        if base is not None and exp is not None:
            normalized["expression"] = f"{base} ** {exp}"
        elif base is not None:
            normalized["expression"] = str(base)
    
    # Handle nested 'tool_args' that contains actual arguments
    if "tool_args" in normalized and isinstance(normalized["tool_args"], dict):
        nested = normalized.pop("tool_args")
        if isinstance(nested, dict):
            for k, v in nested.items():
                if k in param_map and k not in normalized:
                    normalized[k] = v

    return normalized


def fix_calculator_args(t_name: str, t_args: dict, user_input: str, prior_results: list[str]) -> dict:
    """
    Detect when a model passes a plain number as a calculator expression
    (e.g. expression='83521') when the question implies a further operation
    like sqrt. Rewrites the expression to the correct form.
    
    Also handles cases where the model uses alternative argument names
    like 'base'/'exponent' instead of 'expression'.
    
    Also detects REDUNDANT calls where expression is just a prior result
    and marks them with _skip=True to avoid unnecessary tool calls.
    """
    if t_name != "calculator":
        return t_args
    
    t_args = dict(t_args)
    
    # Handle alternative argument formats for power/exponent operations
    if "expression" not in t_args:
        base = t_args.get("base") or t_args.get("number") or t_args.get("x") or t_args.get("value")
        exp = t_args.get("exponent") or t_args.get("power") or t_args.get("n") or t_args.get("p")
        
        if base is not None:
            if exp is not None:
                t_args["expression"] = f"{base} ** {exp}"
            else:
                t_args["expression"] = str(base)
    
    expr = t_args.get("expression", "")
    
    # Check if expression is just a plain number
    try:
        num_val = float(expr)
    except (ValueError, TypeError):
        return t_args

    # Check for redundant call
    for result in prior_results:
        result_clean = strip_tool_prefix(result)
        try:
            result_num = float(result_clean)
            if abs(num_val - result_num) < 0.001:
                t_args["_redundant"] = True
                t_args["_prior_result"] = result_clean
                return t_args
        except (ValueError, TypeError):
            continue

    # Not redundant - check if question implies further operation
    q = user_input.lower()
    if "sqrt" in q or "square root" in q:
        t_args["expression"] = f"sqrt({expr})"
    return t_args


def convert_to_pystrftime(format_str: str) -> str:
    """
    Convert common date format patterns to Python strftime format.
    
    Handles formats like:
    - YYYY-MM-DD -> %Y-%m-%d
    - DD/MM/YYYY -> %d/%m/%Y
    - MM-DD-YYYY -> %m-%d-%Y
    - ISO -> %Y-%m-%d
    """
    replacements = [
        ("YYYY", "%Y"),
        ("YY", "%y"),
        ("MM", "%m"),
        ("DD", "%d"),
        ("HH", "%H"),
        ("mm", "%M"),
        ("ss", "%S"),
        ("ISO", "%Y-%m-%d"),
        ("iso", "%Y-%m-%d"),
    ]
    
    result = format_str
    for pattern, strftime in replacements:
        result = result.replace(pattern, strftime)
    
    if result == format_str:
        if "%" in format_str:
            return format_str
        return "%Y-%m-%d"
    
    return result


def generate_helpful_error_message(tool_name: str, tool, provided_args: dict, error_msg: str) -> str:
    """
    Generate a helpful error message that shows the correct usage format
    when a tool call fails due to incorrect arguments.
    """
    if tool is None:
        return f"[Tool error] {error_msg}"
    
    params_desc = []
    for p in tool.params:
        req_marker = "*" if p.required else ""
        params_desc.append(f"{p.name}{req_marker}: {p.type}")
    
    examples = {
        "calculator": 'calculator(expression="15 * 8")',
        "write_file": 'write_file(path="/tmp/file.txt", content="Hello")',
        "read_file": 'read_file(path="/tmp/file.txt")',
        "python_repl": 'python_repl(code="print(2**10)")',
        "shell": 'shell(command="echo Hello")',
        "web-search": 'web-search(query="capital of France")',
        "get_weather": 'get_weather(city="Tokyo")',
        "convert_currency": 'convert_currency(amount=100, from_currency="USD", to_currency="EUR")',
    }
    
    example = examples.get(tool_name, f"{tool_name}(appropriate_arguments)")
    
    provided_str = ", ".join(f"{k}={v!r}" for k, v in provided_args.items()) if provided_args else "nothing"
    expected_str = ", ".join(params_desc)
    
    return (
        f"[Tool error] Incorrect arguments for {tool_name}.\n"
        f"  Expected: {expected_str}\n"
        f"  You provided: {provided_str}\n"
        f"  Correct example: {example}\n"
        f"  Please retry with the correct argument names."
    )


def synthesize_missing_args(tool_name: str, args: dict, user_input: str, prior_results: list[str], tools_registry) -> dict:
    """
    Try to fill in missing required arguments from context.
    This helps small models that call tools with incomplete arguments.
    """
    from .prompts import PLATFORM_DIR_CMD, PLATFORM_LIST_CMD
    
    tool = tools_registry.get(tool_name) if tools_registry else None
    if tool is None:
        return args
    
    args = dict(args)
    required_params = {p.name for p in tool.params if p.required}
    missing = required_params - set(args.keys())
    
    if not missing:
        return args
    
    q_lower = user_input.lower()
    
    # Tool-specific synthesis
    if tool_name == "calculator" and "expression" in missing:
        numbers = re.findall(r'\d+\.?\d*', user_input)
        operators = re.findall(r'[+\-*/^]', user_input)
        
        if "sqrt" in q_lower or "square root" in q_lower:
            if numbers:
                args["expression"] = f"sqrt({numbers[-1]})"
        elif "power" in q_lower or "^" in user_input:
            if len(numbers) >= 2:
                args["expression"] = f"{numbers[0]} ** {numbers[1]}"
        elif "times" in q_lower or "multiply" in q_lower or "multiplied" in q_lower:
            if len(numbers) >= 2:
                args["expression"] = f"{numbers[0]} * {numbers[1]}"
        elif "divided" in q_lower or "divide" in q_lower:
            if len(numbers) >= 2:
                args["expression"] = f"{numbers[0]} / {numbers[1]}"
        elif "plus" in q_lower or "add" in q_lower or "sum" in q_lower:
            if len(numbers) >= 2:
                args["expression"] = f"{numbers[0]} + {numbers[1]}"
        elif "minus" in q_lower or "subtract" in q_lower:
            if len(numbers) >= 2:
                args["expression"] = f"{numbers[0]} - {numbers[1]}"
        elif numbers and operators:
            expr_parts = []
            for i, num in enumerate(numbers):
                expr_parts.append(num)
                if i < len(operators):
                    expr_parts.append(operators[i])
            args["expression"] = " ".join(expr_parts)
        elif numbers:
            args["expression"] = numbers[0]
    
    elif tool_name == "python_repl" and "code" in missing:
        if "date" in q_lower and "time" in q_lower:
            args["code"] = "from datetime import datetime\nprint(datetime.now().strftime('Today is %A, %B %d, %Y and the time is %I:%M %p.'))"
        elif "date" in q_lower:
            provided_format = args.get("format", args.get("date_format", ""))
            if provided_format:
                py_format = convert_to_pystrftime(provided_format)
                args["code"] = f"from datetime import datetime\nprint(datetime.now().strftime('{py_format}'))"
            else:
                args["code"] = "from datetime import datetime\nprint(datetime.now().strftime('Today is %A, %B %d, %Y.'))"
        elif "time" in q_lower:
            args["code"] = "from datetime import datetime\nprint(datetime.now().strftime('The current time is %I:%M %p.'))"
        else:
            args["code"] = "from datetime import datetime\nprint(datetime.now())"
    
    elif tool_name == "shell" and "command" in missing:
        if "directory" in q_lower or "folder" in q_lower:
            args["command"] = PLATFORM_DIR_CMD
        elif "files" in q_lower and "list" in q_lower:
            args["command"] = PLATFORM_LIST_CMD
    
    elif tool_name == "write_file" and prior_results:
        if "content" in missing and "path" in args:
            args["content"] = strip_tool_prefix(prior_results[-1])
    
    return args
