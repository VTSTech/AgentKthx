"""
⚛️ AgentKthx — Tool Parser
Regex patterns and functions for parsing tool calls from model output.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

# Import canonical ToolCall from models.py (single source of truth)
from .models import ToolCall


# ------------------------------------------------------------------ #
#  Regex patterns for ReAct parsing                                   #
# ------------------------------------------------------------------ #

_THOUGHT_RE = re.compile(r"Thought:\s*(.*?)(?=Action:|Final Answer:|$)", re.DOTALL | re.IGNORECASE)
_ACTION_RE = re.compile(
    r"Action:\s*[`\"']?(\w+)[`\"']?\s*\n?\s*Action Input:\s*(.*?)(?=\n\s*(?:Observation:|Thought:|Final Answer:|Action:|Example)|$)",
    re.DOTALL | re.IGNORECASE
)
_ACTION_RE_SAMELINE = re.compile(
    r"Action:\s*[`\"']?(\w+)[`\"']?\s+Action Input:\s*(.*?)(?=\n\s*(?:Observation:|Thought:|Final Answer:|Action:|Example)|$)",
    re.DOTALL | re.IGNORECASE
)
_FINAL_RE = re.compile(r"Final Answer:\s*(.*?)$", re.DOTALL | re.IGNORECASE)
_PYTHON_CODE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


# ------------------------------------------------------------------ #
#  JSON sanitization and extraction                                    #
# ------------------------------------------------------------------ #

def _sanitize_model_json(text: str) -> str:
    """
    Fix common JSON mistakes made by small (0.5b-3b) models before parsing.

    1. Python bool/None literals to JSON equivalents:
       True -> true,  False -> false,  None -> null

    2. Python string concatenation in values - keep only the string literal:
       "Today: " + datetime.now().strftime(...)  becomes  "Today: "

    3. Trailing commas before } or ] (technically invalid JSON)

    4. Over-escaped backslashes (Windows paths): \\\\ -> \\
    """
    text = re.sub(r':\s*True\b', ': true', text)
    text = re.sub(r':\s*False\b', ': false', text)
    text = re.sub(r':\s*None\b', ': null', text)
    text = re.sub(r'\[\s*True\b', '[true', text)
    text = re.sub(r'\[\s*False\b', '[false', text)
    text = re.sub(r'\[\s*None\b', '[null', text)
    text = re.sub(r'("(?:[^"\\]|\\.)*")\s*\+\s*[^,\'"}\]\n]+', r'\1', text)
    text = re.sub(r',\s*([}\]])', r'\1', text)
    
    # Fix over-escaped backslashes in Windows paths
    # Models often output: "C:\\\\Users\\\\..." instead of "C:\\Users\\..."
    # Replace 4+ backslashes with 2
    text = re.sub(r'\\\\\\\\', r'\\\\', text)
    # Replace 3 backslashes with 2 (odd number issue)
    text = re.sub(r'\\\\\\(?=[^\\"])', r'\\\\', text)

    return text


def _extract_tool_from_json(obj: dict, debug: bool = False) -> tuple[str | None, dict | None]:
    """Extract tool name and args from a parsed JSON object."""
    # Guard against non-dict objects (e.g., JSON parsed as int, str, list)
    if not isinstance(obj, dict):
        if debug:
            print(f"    _extract_tool_from_json: obj is not a dict, got {type(obj).__name__}")
        return None, None

    # Try standard keys first
    name = obj.get("name") or obj.get("function") or obj.get("tool") or obj.get("action")
    args = obj.get("arguments") or obj.get("parameters") or obj.get("args") or obj.get("actionInput") or {}

    # Handle JSON-wrapped ReAct format: {"Action": "tool_name", "Action Input": {...}}
    # or {"action": "tool_name", "action_input": {...}}
    # This is what dolphin3.0-qwen2.5:0.5b and qwen2.5-coder:0.5b produce
    for key in obj.keys():
        key_lower = key.lower()
        # Check for action key (if not already found)
        if key_lower == "action" and isinstance(obj[key], str) and not name:
            name = obj[key]
        # Check for action_input key (various formats)
        elif key_lower in ("action input", "actioninput", "action_input"):
            potential_args = obj[key]
            if isinstance(potential_args, dict):
                args = potential_args
            elif potential_args:
                args = {"input": potential_args}

    # If we found action but no args, also check for action_input as nested key
    if name and not args:
        for key in obj.keys():
            if key.lower() in ("action input", "actioninput", "action_input"):
                potential_args = obj[key]
                if isinstance(potential_args, dict):
                    args = potential_args
                elif potential_args:
                    args = {"input": potential_args}
                break

    # Handle bare argument objects - ONLY if the JSON looks like a tool call
    # CRITICAL: "query" is removed because models often output JSON with "query"
    # as a general key (e.g., {"query": "...", "response": "...", "method": "..."})
    # which should NOT be interpreted as a web-search tool call.
    # Only "expression" and "command" are unambiguous indicators of tool usage.
    if not name:
        arg_to_tool = {
            "expression": "calculator",  # Only unambiguous calculator indicator
            "command": "shell",          # Only unambiguous shell indicator
        }
        # Only map to tool if the object has ONLY tool-related keys (not response/method/etc)
        obj_keys = set(obj.keys())
        non_tool_keys = {"response", "method", "answer", "result", "explanation", "output", "text"}
        if not obj_keys.intersection(non_tool_keys):
            for key in obj.keys():
                if key in arg_to_tool:
                    name = arg_to_tool[key]
                    args = obj
                    break

    if not name or not isinstance(args, dict):
        if debug:
            print(f"    _extract_tool_from_json: no name or args")
        return None, None

    # Detect if the 'name' field contains code instead of a tool name
    code_indicators = ["(", ")", "+", "-", "*", "/", "=", "[", "]", "print", "def ", "return"]
    if any(indicator in name for indicator in code_indicators):
        return name, args

    return name, args


# ------------------------------------------------------------------ #
#  ReAct parser                                                       #
# ------------------------------------------------------------------ #

def _parse_react(text: str, tool_names: list[str] | None = None) -> tuple[str | None, str | None, dict | None, str | None]:
    """
    Returns (thought, tool_name, tool_args, final_answer).
    Any field may be None if not present.
    
    Handles multiple format variations from small models.
    """
    # Fix repetition issues from small models (qwen3:0.6b, etc.)
    from .helpers import detect_and_fix_repetition
    text = detect_and_fix_repetition(text)
    
    thought = None
    tool_name = None
    tool_args = None
    final_answer = None

    # Extract thought
    m = _THOUGHT_RE.search(text)
    if m:
        thought = m.group(1).strip()

    # Try to extract action - try multiple patterns
    m = _ACTION_RE.search(text)
    if not m:
        m = _ACTION_RE_SAMELINE.search(text)
    
    if m:
        tool_name = m.group(1).strip()
        raw_args = m.group(2).strip()
        
        # Strip any trailing backticks or quotes from tool name
        tool_name = tool_name.strip('`"\'')
        
        # OpenResponses: NO FUZZY MATCHING
        # Tool names must match exactly per spec requirement:
        # "allowed_tools: Hard constraint - Server MUST reject/suppress calls to tools not in this list"
        # Error recovery module will provide hints to guide the model.
        
        # Extract just the JSON object from raw_args (handle extra text after)
        json_start = raw_args.find('{')
        if json_start != -1:
            # Find matching closing brace
            depth = 0
            json_end = -1
            for i, ch in enumerate(raw_args[json_start:], json_start):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        json_end = i
                        break
            if json_end != -1:
                raw_args = raw_args[json_start:json_end + 1]
        
        # Try to parse JSON args
        try:
            tool_args = json.loads(raw_args)
            # Ensure tool_args is a dict - json.loads can return str, list, etc.
            if not isinstance(tool_args, dict):
                tool_args = {"input": str(tool_args)}
        except json.JSONDecodeError:
            sanitized = _sanitize_model_json(raw_args)
            try:
                tool_args = json.loads(sanitized)
                # Ensure tool_args is a dict
                if not isinstance(tool_args, dict):
                    tool_args = {"input": str(tool_args)}
            except json.JSONDecodeError:
                # SEC-02 (R07.05): the previous fallback used
                # ``ast.literal_eval`` to accept Python dict literals with
                # single quotes (``{'expression': '15 + 27'}``) that small
                # models (qwen2.5:0.5b, BitNet) emit. ``ast.literal_eval``
                # also accepts ``bytes`` (``b'...'``), ``complex``,
                # ``frozenset``, ``tuple``, and ``set`` — none of which the
                # tool schema expects. A malicious prompt injection that
                # placed ``Action Input: {b'file_path': b'/etc/passwd'}``
                # could feed ``bytes``-typed args to tool handlers that
                # bypass ``validate_path``'s string-prefix checks (``os``
                # accepts ``bytes`` for path operations). The fix: convert
                # Python single-quote dict syntax to JSON before parsing,
                # instead of using ``ast.literal_eval``.
                if raw_args.startswith('{') and raw_args.endswith('}'):
                    py_to_json = raw_args
                    # Single→double quoted strings (avoid already-doubled)
                    py_to_json = re.sub(
                        r"(?<!\\)'((?:[^'\\]|\\.)*)'",
                        r'"\1"',
                        py_to_json,
                    )
                    # Python bool/None → JSON equivalents
                    py_to_json = re.sub(r'\bTrue\b', 'true', py_to_json)
                    py_to_json = re.sub(r'\bFalse\b', 'false', py_to_json)
                    py_to_json = re.sub(r'\bNone\b', 'null', py_to_json)
                    try:
                        tool_args = json.loads(py_to_json)
                        if not isinstance(tool_args, dict):
                            tool_args = {"input": str(tool_args)}
                    except json.JSONDecodeError:
                        tool_args = None

                if tool_args is None:
                    # Last resort: check for known patterns in the raw text
                    # (works for both single and double quoted values)
                    expr_match = re.search(r'[\'"]expression[\'"]:\s*[\'"]([^\'"]+)[\'"]', raw_args)
                    if expr_match:
                        tool_args = {"expression": expr_match.group(1)}
                    elif raw_args.startswith('{') and '=' in raw_args and 'arguments' not in raw_args.lower():
                        tool_args = {"input": raw_args}
                    else:
                        tool_args = {"input": raw_args}

    # Extract final answer
    fa_match = _FINAL_RE.search(text)
    if fa_match:
        final_answer = fa_match.group(1).strip()

    return thought, tool_name, tool_args, final_answer


# ------------------------------------------------------------------ #
#  ToolParser Class                                                   #
# ------------------------------------------------------------------ #

class ToolParser:
    """
    Parse tool calls from model output.

    Supports multiple formats:
    - Native function calling (JSON)
    - ReAct format: Action: tool_name\nAction Input: {...}
    - XML format: <tool>name</tool><args>{...}</args>
    - Markdown code blocks with JSON
    """

    def __init__(self, tool_names: list[str] | None = None):
        """
        Initialize parser with known tool names for fuzzy matching.

        Args:
            tool_names: List of valid tool names
        """
        self.tool_names = set(tool_names or [])

    def parse(self, text: str) -> list[ToolCall]:
        """
        Parse all tool calls from text.

        OpenResponses: Only parses EXPLICIT tool call formats from the model.
        No fallbacks that synthesize tool calls from content.

        Supported formats:
        1. Native JSON function calls (for models with native tool support)
        2. ReAct format: Action: tool_name\nAction Input: {...}
        3. XML format: <tool>name</tool><args>{...}</args>

        IMPORTANT: The model must explicitly format tool calls. JSON in markdown
        code blocks or elsewhere in text is NOT parsed as a tool call unless
        the model uses explicit Action/Action Input format.

        Args:
            text: Model output text

        Returns:
            List of parsed ToolCalls
        """
        calls = []

        # Try native JSON first (for models with function calling)
        calls.extend(self._parse_native_json(text))

        # Try ReAct format (explicit Action/Action Input)
        calls.extend(self._parse_react(text))

        # Try XML format (explicit <tool> tags)
        calls.extend(self._parse_xml(text))

        # NO FALLBACKS - tool calls must come from the model explicitly

        return calls

    def _parse_native_json(self, text: str) -> list[ToolCall]:
        """Parse native JSON function calling format.

        Handles:
        - Raw JSON objects/arrays
        - JSON in markdown code blocks (```json ... ```)
        - JSON-wrapped ReAct format: {"Action": "tool", "Action Input": {...}}
        """
        calls = []

        # Check for Final Answer in the text (may appear alongside JSON tool call)
        final_answer = None
        fa_match = _FINAL_RE.search(text)
        if fa_match:
            final_answer = fa_match.group(1).strip()

        # First, try to extract JSON from markdown code blocks
        json_from_codeblock = self._extract_json_from_codeblock(text)
        if json_from_codeblock:
            for json_str in json_from_codeblock:
                try:
                    data = json.loads(json_str)
                    call = self._extract_tool_from_json(data)
                    if call:
                        # Attach final_answer if present
                        call.final_answer = final_answer
                        calls.append(call)
                except json.JSONDecodeError:
                    continue

        # Also try parsing entire text as JSON array/object
        try:
            # Try parsing entire text as JSON array
            if text.strip().startswith("["):
                items = json.loads(text)
                for item in items:
                    call = self._extract_tool_from_json(item)
                    if call:
                        call.final_answer = final_answer
                        calls.append(call)

            # Try parsing as single JSON object
            elif text.strip().startswith("{"):
                data = json.loads(text)
                call = self._extract_tool_from_json(data)
                if call:
                    call.final_answer = final_answer
                    calls.append(call)
        except json.JSONDecodeError:
            pass

        return calls

    def _extract_json_from_codeblock(self, text: str) -> list[str]:
        """Extract JSON strings from markdown code blocks."""
        json_strings = []

        # Pattern for markdown code blocks with optional language specifier
        codeblock_pattern = re.compile(
            r'```(?:json)?\s*\n(.*?)```',
            re.DOTALL | re.IGNORECASE
        )

        for match in codeblock_pattern.finditer(text):
            content = match.group(1).strip()
            # Check if it looks like JSON
            if content.startswith('{') or content.startswith('['):
                json_strings.append(content)

        return json_strings

    def _extract_tool_from_json(self, data: dict) -> ToolCall | None:
        """Extract ToolCall from JSON object.
        
        OpenResponses: NO FUZZY MATCHING - tool names must match exactly.
        """
        name, args = _extract_tool_from_json(data)
        if name:
            # OpenResponses: Return tool name exactly as specified by model
            # No fuzzy matching - spec requires exact tool name matching
            return ToolCall(
                name=name,
                arguments=args if isinstance(args, dict) else {},
                raw=str(data),
            )

        return None

    def _parse_react(self, text: str) -> list[ToolCall]:
        """Parse ReAct format tool calls."""
        thought, name, args, final = _parse_react(text, list(self.tool_names))
        
        if name:
            return [ToolCall(
                name=name,
                arguments=args or {},
                raw=f"Action: {name}\nAction Input: {args}",
                confidence=0.9 if name in self.tool_names else 0.7,
                final_answer=final,  # Include final answer if present
                thought=thought,  # OpenResponses: Include captured thought
            )]
        
        return []

    def _parse_xml(self, text: str) -> list[ToolCall]:
        """Parse XML format tool calls."""
        calls = []

        # XML patterns
        xml_tool_pattern = re.compile(
            r"<tool>\s*(\w+)\s*</tool>",
            re.IGNORECASE
        )
        xml_args_pattern = re.compile(
            r"<(?:args|arguments|params)>\s*([\s\S]*?)\s*</(?:args|arguments|params)>",
            re.IGNORECASE
        )

        tools = list(xml_tool_pattern.finditer(text))
        args_matches = list(xml_args_pattern.finditer(text))

        for i, tool_match in enumerate(tools):
            name = tool_match.group(1).strip()
            # OpenResponses: NO FUZZY MATCHING - tool names must match exactly

            args = {}
            if i < len(args_matches):
                args_text = args_matches[i].group(1).strip()
                try:
                    args = json.loads(args_text)
                except json.JSONDecodeError:
                    args = {"input": args_text}

            calls.append(ToolCall(
                name=name,
                arguments=args if isinstance(args, dict) else {},
                raw=tool_match.group(0),
            ))

        return calls

    def is_final_answer(self, text: str) -> bool:
        """Check if text indicates a final answer.
        
        IMPORTANT: This must be conservative - only match explicit ReAct format markers.
        Overly broad patterns like "Result:" or "Therefore," cause small models to
        bypass tool calling when they shouldn't.
        """
        # Only match explicit ReAct format markers (matching main branch behavior)
        patterns = [
            r"Final Answer:",  # Standard ReAct marker
        ]

        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True

        return False

    def extract_final_answer(self, text: str) -> str:
        """Extract final answer from text.

        Only extracts after the "Final Answer:" marker to stay consistent with
        is_final_answer(). Falls back to "Answer:" and "Result:" only if no
        "Final Answer:" marker is found, preferring the more specific match.
        """
        # Primary: use the same conservative marker as is_final_answer()
        match = re.search(r"Final Answer:\s*([\s\S]+)", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

        # Fallback: try broader patterns if no explicit Final Answer marker
        for pattern in [r"Answer:\s*([\s\S]+)", r"Result:\s*([\s\S]+)"]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        # Return entire text as answer
        return text.strip()

