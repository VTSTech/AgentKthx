"""
⚛️ AgentKthx — Prompts
Argument aliases, platform constants, and few-shot examples for small model support.

Written by VTSTech — https://www.vts-tech.org
"""

import platform as _platform

# ------------------------------------------------------------------ #
#  Platform detection                                                  #
# ------------------------------------------------------------------ #

_IS_WINDOWS = _platform.system() == "Windows"
PLATFORM_DIR_CMD = "cd" if _IS_WINDOWS else "pwd"
PLATFORM_LIST_CMD = "dir" if _IS_WINDOWS else "ls"

# ------------------------------------------------------------------ #
#  Tool-specific argument aliases (for small model hallucinations)    #
# ------------------------------------------------------------------ #
# Small models often hallucinate argument names. This mapping helps
# convert common hallucinations to the correct argument names.

TOOL_ARG_ALIASES = {
    "calculator": {
        # Common hallucinations for expression
        "a": "expression", "b": "expression", "x": "expression", "y": "expression",
        "num": "expression", "number": "expression",
        "input": "expression", "formula": "expression", "math": "expression",
        "expr": "expression", "calc": "expression", "result": "expression",
        "param": "expression", "args": "expression", "arg": "expression",
        # Power operations - combine into expression
        "base": "_combine_power", "exponent": "_combine_power", "power": "_combine_power",
        "n": "_combine_power", "p": "_combine_power", "exp": "_combine_power",
    },
    "python_repl": {
        "code": "code",  # correct
        "script": "code", "cmd": "code", "command": "code",
        "python": "code", "py": "code", "exec": "code", "execute": "code",
        "expression": "code", "expr": "code", "statement": "code",
        "program": "code", "source": "code", "input": "code",
    },
    "write_file": {
        "path": "file_path",  # actual param name is file_path
        "filepath": "file_path", "file_path": "file_path", "filename": "file_path",
        "file": "file_path", "dest": "file_path", "destination": "file_path",
        "output_path": "file_path", "outputfile": "file_path", "location": "file_path",
        "content": "content",  # correct
        "data": "content", "text": "content", "body": "content",
        "output": "content", "string": "content", "value": "content",
        "write": "content", "output_data": "content",
    },
    "read_file": {
        "path": "file_path",  # actual param name is file_path
        "filepath": "file_path", "file_path": "file_path", "filename": "file_path",
        "file": "file_path", "input": "file_path", "source": "file_path", "location": "file_path",
    },
    "list_directory": {
        "path": "path",  # correct
        "dir": "path", "directory": "path", "folder": "path",
        "dir_path": "path", "directory_path": "path", "folder_path": "path",
        "location": "path",
    },
    "shell": {
        "command": "command",  # correct
        "cmd": "command", "exec": "command", "shell_cmd": "command",
        "bash": "command", "script": "command", "instruction": "command",
        "run": "command", "execute": "command", "op": "command",
        "text": "command", "input": "command", "arg": "command",
        "args": "command", "str": "command", "value": "command",
    },
    "web-search": {
        "query": "query",  # correct
        "search": "query", "q": "query", "term": "query", "search_query": "query",
        "keywords": "query", "text": "query", "input": "query",
    },
    "get_weather": {
        "city": "city",  # correct
        "location": "city", "place": "city", "town": "city",
        "where": "city", "area": "city", "region": "city",
    },
    "convert_currency": {
        "amount": "amount",  # correct
        "from_currency": "from_currency",  # correct
        "to_currency": "to_currency",  # correct
        # Common variations
        "from": "from_currency", "to": "to_currency",
        "source_currency": "from_currency", "target_currency": "to_currency",
        "money": "amount", "value": "amount", "price": "amount",
    },
    "edit_file": {
        # Common hallucinations for file_path
        "file_path": "file_path",  # correct
        "path": "file_path", "filepath": "file_path", "file": "file_path",
        "filename": "file_path", "source": "file_path",
        # Common hallucinations for old_string
        "old_string": "old_string",  # correct
        "old": "old_string", "find": "old_string", "search": "old_string",
        "replace": "old_string", "target": "old_string", "original": "old_string",
        "before": "old_string", "from": "old_string", "match": "old_string",
        "old_text": "old_string", "old_content": "old_string",
        # Common hallucinations for new_string
        "new_string": "new_string",  # correct
        "new": "new_string", "replacement": "new_string", "with": "new_string",
        "to": "new_string", "after": "new_string", "replacement_text": "new_string",
        "new_text": "new_string", "new_content": "new_string",
        # Common hallucinations for replace_all
        "replace_all": "replace_all",  # correct
        "all": "replace_all", "global": "replace_all", "everywhere": "replace_all",
    },
    "todo": {
        # Common hallucinations for action
        "action": "action",  # correct
        "act": "action", "cmd": "action", "op": "action",
        "command": "action", "do": "action", "type": "action",
        # Common hallucinations for content
        "content": "content",  # correct
        "text": "content", "task": "content", "description": "content",
        "desc": "content", "item": "content", "todo": "content",
        "message": "content", "value": "content", "input": "content",
        # Common hallucinations for task_id
        "task_id": "task_id",  # correct
        "id": "task_id", "todo_id": "task_id",
        # Common hallucinations for priority
        "priority": "priority",  # correct
        "pri": "priority", "level": "priority", "importance": "priority",
    },
}

# Aliases that are too generic and should only be applied when the args dict
# has no other parameters that already matched a real expected param.
# These are the aliases most likely to misinterpret legitimate model output.
CONTEXTUAL_ALIASES = {
    "calculator": {"value", "input", "result", "n", "p", "exp"},
    "shell": {"text", "input", "arg", "args", "str", "value"},
    "write_file": {"value", "string"},
    "read_file": {"input"},
    "web-search": {"text", "input"},
    "python_repl": {"input", "expression", "expr"},
    "convert_currency": {"value"},
    "edit_file": {"old", "new", "file", "source", "target"},
    "todo": {"text", "task", "item", "value", "input", "desc"},
}

# ------------------------------------------------------------------ #
#  Few-shot prompting suffix for small models                         #
# ------------------------------------------------------------------ #
# Added to system prompt when using models < 2B parameters

FEW_SHOT_SUFFIX = f"""

═══════════════════════════════════════════════════════════════
TOOL USAGE EXAMPLES - Follow this EXACT format:
═══════════════════════════════════════════════════════════════

Example 1 - Multiplication:
Thought: I need to multiply 15 times 8
Action: calculator
Action Input: {{"expression": "15 * 8"}}

Example 2 - Power:
Thought: I need to calculate 2 to the power of 20
Action: calculator
Action Input: {{"expression": "2 ** 20"}}

Example 3 - Echo text:
Thought: User wants to print some text
Action: shell
Action Input: {{"command": "echo Hello World"}}

Example 4 - Current directory:
Thought: User wants to know the current directory
Action: shell
Action Input: {{"command": "{PLATFORM_DIR_CMD}"}}

Example 5 - Run Python code:
Thought: I need to compute something in Python
Action: python_repl
Action Input: {{"code": "print(2 ** 10)"}}

Example 6 - Write to file:
Thought: User wants to save text to a file
Action: write_file
Action Input: {{"file_path": "/tmp/test.txt", "content": "Hello World"}}

Example 7 - Read a file:
Thought: User wants to see file contents
Action: read_file
Action Input: {{"file_path": "/tmp/test.txt"}}

Example 8 - List directory:
Thought: User wants to see files in a directory
Action: list_directory
Action Input: {{"path": "/tmp"}}

Example 9 - Get date:
Thought: User wants to know today's date
Action: get_date
Action Input: {{}}

Example 10 - Get time:
Thought: User wants to know current time
Action: get_time
Action Input: {{}}

CRITICAL RULES:
1. Action line: just the tool name (no backticks, no quotes)
2. Action Input: valid JSON with correct argument names for THAT tool
3. ARGUMENT NAMES BY TOOL:
   - calculator: {{"expression": "15 * 8"}}
   - shell: {{"command": "echo Hello"}}
   - python_repl: {{"code": "print(result)"}}
   - write_file: {{"file_path": "/path/file.txt", "content": "text to write"}}
   - read_file: {{"file_path": "/path/file.txt"}}
   - list_directory: {{"path": "/tmp"}}
   - get_date: {{}} (no arguments)
   - get_time: {{}} or {{"timezone": "America/New_York"}}
4. MATH OPERATORS: * (multiply), ** (power), / (divide), + (add), - (subtract)
5. NEVER write Observation yourself - wait for the actual result!
═══════════════════════════════════════════════════════════════
"""

# Compact version for models that need minimal prompting
FEW_SHOT_COMPACT = f"""
TOOL EXAMPLES (ReAct format):
Calculator: {{"expression": "15 * 8"}}
Shell: {{"command": "echo Hello World"}}
Current dir: {{"command": "{PLATFORM_DIR_CMD}"}}
Python: {{"code": "print(result)"}}
Write file: {{"file_path": "/tmp/file.txt", "content": "Hello"}}
Read file: {{"file_path": "/tmp/file.txt"}}
List dir: {{"path": "/tmp"}}
Get date: {{}}
Get time: {{}}

ARGUMENT NAMES: expression (calculator), command (shell), code (python_repl), file_path+content (write_file), file_path (read_file), path (list_directory)
MATH: * = multiply, ** = power, / = divide
NEVER write Observation yourself - wait for real result!
"""


def get_system_prompt(
    model_name: str,
    tool_support: str = "react",
    tools: list | None = None,
) -> str:
    """
    Get the appropriate system prompt for a model.

    NOTE: This function is kept for backward compatibility.
    The Agent class now uses soul-based prompting by default.

    Args:
        model_name: Name of the model
        tool_support: Tool support level (ignored, kept for compatibility)
        tools: List of available tools

    Returns:
        System prompt string
    """
    if tools:
        tool_prompt = get_tool_prompt(tools)
        return f"You are a helpful AI assistant.\n\n{tool_prompt}"
    return "You are a helpful AI assistant."


def get_tool_prompt(tools: list, tool_support: str = "react", family: str | None = None) -> str:
    """
    Generate tool description prompt.

    When tool_support is "native", skips ReAct format instructions since
    tools are passed via the API body and the model uses native function calling.

    Args:
        tools: List of available tools
        tool_support: Tool support level ("native", "react", "untested")
        family: Model family (reserved for future use)

    Returns:
        Tool description string
    """
    if not tools:
        return ""

    # In native mode, the API passes tools via the tools parameter.
    # Skip ReAct format instructions — the model knows how to call tools natively.
    is_native = tool_support.lower() in ("native", "openai")

    lines = ["## Available Tools\n"]

    if not is_native:
        lines.append("When you need to use a tool, follow this EXACT format:\n")
        lines.append("```")
        lines.append("Thought: <brief reasoning>")
        lines.append("Action: <tool_name>")
        lines.append("Action Input: <JSON arguments>")
        lines.append("```\n")

    lines.append("| Tool | Description | Arguments |")
    lines.append("|------|-------------|-----------|")

    for tool in tools:
        # Get tool name and description
        name = getattr(tool, 'name', str(tool))
        desc = getattr(tool, 'description', '')
        params = getattr(tool, 'params', [])
        
        # Build arguments example
        if params:
            param_pairs = []
            for p in params:
                p_name = getattr(p, 'name', str(p))
                p_type = getattr(p, 'type', 'string')
                if p_type == 'string':
                    param_pairs.append(f'"{p_name}": "..."')
                elif p_type in ('number', 'integer', 'float'):
                    param_pairs.append(f'"{p_name}": 0')
                else:
                    param_pairs.append(f'"{p_name}": ...')
            args_example = "{" + ", ".join(param_pairs) + "}"
        else:
            args_example = "{}"

        # Truncate description if too long for table
        short_desc = desc.split('.')[0] if desc else "No description"
        if len(short_desc) > 50:
            short_desc = short_desc[:47] + "..."

        lines.append(f"| `{name}` | {short_desc} | `{args_example}` |")

    lines.append("")
    lines.append("**CRITICAL RULE**: If a tool is NOT in the available tools list, do NOT try to use it. Respond directly instead.")

    if not is_native:
        # ReAct format instructions — only for text-parsing models
        lines.append("")
        lines.append("After tool execution, provide the Final Answer:")
        lines.append("```")
        lines.append("Thought: I have the result")
        lines.append("Final Answer: <the answer>")
        lines.append("```")
        # Add few-shot examples for better tool usage
        lines.append(FEW_SHOT_COMPACT)

    return "\n".join(lines)


def get_react_prompt(
    question: str,
    tools: list | None = None,
    scratchpad: str = "",
) -> str:
    """
    Generate a ReAct prompt for the given question.

    Args:
        question: User question
        tools: Available tools
        scratchpad: Previous reasoning/observations

    Returns:
        Complete ReAct prompt
    """
    tool_desc = get_tool_prompt(tools or [])

    prompt = f"""{tool_desc}

Question: {question}
"""

    if scratchpad:
        prompt += f"\n{scratchpad}\n"

    return prompt


__all__ = [
    "PLATFORM_DIR_CMD",
    "PLATFORM_LIST_CMD",
    "TOOL_ARG_ALIASES",
    "FEW_SHOT_SUFFIX",
    "FEW_SHOT_COMPACT",
    "get_system_prompt",
    "get_tool_prompt",
    "get_react_prompt",
]