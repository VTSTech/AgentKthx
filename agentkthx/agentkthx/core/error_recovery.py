"""
⚛️ AgentKthx — Error Recovery Module

Implements error recovery state tracking to prevent infinite loops when tools
repeatedly fail, and provides tool-specific observation hints for common errors.

Key Features:
1. ErrorRecoveryTracker - Tracks consecutive failures per tool
2. Tool-specific hints for common error patterns
3. Smart recovery strategies (alternative tools, guidance, early termination)

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional


# ============================================================================
# Configuration
# ============================================================================

# Maximum consecutive failures per tool before suggesting alternatives
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3

# Maximum total failures before terminating the loop (lifetime stat only)
DEFAULT_MAX_TOTAL_FAILURES = 5

# Maximum consecutive ALL-failure steps before terminating the loop.
# Termination uses a *consecutive* semantic: any successful step resets the
# counter, so an agent that alternates errors and successes (a healthy
# exploration pattern) is never killed, while a stuck agent that fails
# every single step is stopped after this many consecutive failures.
DEFAULT_MAX_CONSECUTIVE_ALL_FAILURES = 5

# Maximum times the exact same (tool, arguments) call may fail before the
# harness blocks a re-issue of that identical call. Prevents small models
# from hallucinating a bad argument and then resending it forever.
DEFAULT_MAX_IDENTICAL_FAILURES = 2

# Maximum retries per individual tool call before giving up
DEFAULT_MAX_TOOL_RETRIES = 2

# Default setting for retry-on-error behavior
DEFAULT_RETRY_ON_ERROR = True


# ============================================================================
# Tool-Specific Error Hints
# ============================================================================

# Error pattern -> (hint, suggestion) mappings per tool
TOOL_ERROR_HINTS = {
    "calculator": {
        "syntax": (
            "Use Python syntax for math expressions.",
            "Examples: 2**10 (power), sqrt(144) (root), 15 * 8 (multiply)"
        ),
        "division by zero": (
            "Division by zero is not allowed.",
            "Check your expression for zero divisors."
        ),
        "name .* is not defined": (
            "Unknown function or variable name.",
            "Available: sqrt, sin, cos, tan, log, log10, exp, pi, e, floor, ceil"
        ),
        "exponent.*exceeds": (
            "Exponent too large.",
            "Use a smaller exponent (max 10,000)."
        ),
        "NaN": (
            "Result is not a number (NaN).",
            "Check for invalid operations like sqrt(-1) or log(0)."
        ),
        "infinite": (
            "Result is infinite.",
            "Check for division by very small numbers or overflow."
        ),
    },
    
    "shell": {
        "command not found": (
            "Command not found on this system.",
            "Try: which <command> to find installed tools, or use an alternative."
        ),
        "permission denied": (
            "Permission denied for this operation.",
            "Try a different approach or check if you need different arguments."
        ),
        "no such file or directory": (
            "Path does not exist.",
            "Check the path with 'ls' first, or use absolute paths."
        ),
        "blocked": (
            "This command is blocked for security.",
            "Try an alternative command that is allowed."
        ),
        "injection": (
            "Potential injection pattern detected.",
            "Use simple commands without chaining, pipes, or redirections."
        ),
        "security": (
            "Security restriction prevented execution.",
            "Review the command for blocked patterns."
        ),
        "timed out": (
            "Command took too long.",
            "Try a simpler command or reduce the operation scope."
        ),
    },
    
    "read_file": {
        "not found": (
            "File not found.",
            "Check the path with list_directory, or verify the file exists."
        ),
        "permission denied": (
            "Permission denied to read this file.",
            "Try a different file or check the path."
        ),
        "is a directory": (
            "Path is a directory, not a file.",
            "Use list_directory to see contents, or specify a file path."
        ),
        "security": (
            "Path access denied for security.",
            "Use paths in allowed directories: /tmp, ./output, ./data"
        ),
        "truncated": (
            "File was too large and truncated.",
            "Read specific sections or use a more targeted approach."
        ),
    },
    
    "write_file": {
        "permission denied": (
            "Permission denied to write to this location.",
            "Try writing to /tmp, ./output, or another allowed directory."
        ),
        "security": (
            "Path access denied for security.",
            "Use paths in allowed directories: /tmp, ./output, ./data"
        ),
        "no such file or directory": (
            "Parent directory does not exist.",
            "The parent directory will be created automatically. Try again."
        ),
    },
    
    "list_directory": {
        "not a directory": (
            "Path is a file, not a directory.",
            "Use read_file to read file contents, or specify a directory path."
        ),
        "not found": (
            "Directory not found.",
            "Check the parent path with list_directory on a parent directory."
        ),
        "permission denied": (
            "Permission denied to list this directory.",
            "Try a different directory path."
        ),
        "security": (
            "Path access denied for security.",
            "Use paths in allowed directories."
        ),
    },
    
    "http_get": {
        "connection": (
            "Could not connect to the URL.",
            "Check if the URL is correct and the server is accessible."
        ),
        "http error 4": (
            "Client error (4xx).",
            "Check the URL and parameters. The resource may not exist."
        ),
        "http error 5": (
            "Server error (5xx).",
            "The server encountered an error. Try again later."
        ),
        "ssrf": (
            "URL blocked for security (SSRF protection).",
            "Only external public URLs are allowed."
        ),
        "timeout": (
            "Request timed out.",
            "The server took too long to respond. Try a simpler request."
        ),
        "too large": (
            "Response too large and was truncated.",
            "Use a more specific URL or query to get smaller responses."
        ),
    },
    
    "python_repl": {
        "syntaxerror": (
            "Python syntax error.",
            "Check for missing quotes, parentheses, or incorrect indentation."
        ),
        "nameerror": (
            "Undefined variable or function.",
            "Available: math, json, re, datetime, collections, itertools"
        ),
        "importerror": (
            "Module not available in sandbox.",
            "Only safe modules are allowed: math, json, re, datetime, collections, itertools"
        ),
        "timeout": (
            "Code execution timed out.",
            "Simplify the code or reduce iterations."
        ),
        "permission": (
            "File/network access blocked in sandbox.",
            "The Python REPL is sandboxed. No file or network operations."
        ),
    },
    
    "get_time": {
        "unknown timezone": (
            "Unknown timezone identifier.",
            "Use IANA timezone names like 'UTC', 'America/New_York', 'Europe/London'"
        ),
    },
    
    "parse_json": {
        "expecting": (
            "Invalid JSON format.",
            "Check for missing quotes, commas, or brackets."
        ),
        "unterminated": (
            "JSON string not properly closed.",
            "Ensure all strings have matching quotes."
        ),
    },
}

# Generic hints that apply to any tool
GENERIC_ERROR_HINTS = {
    "error": (
        "An error occurred.",
        "Try a different approach or check the arguments."
    ),
    "unknown tool": (
        "Tool not found.",
        "Check available tools and use the exact tool name."
    ),
    "missing": (
        "Missing required argument.",
        "Check the tool's required parameters."
    ),
}


# ============================================================================
# Tool Name Suggestions (OpenResponses Strict Compliance)
# ============================================================================

# When model hallucinates a tool name, suggest the correct tool
# This provides guidance WITHOUT auto-correcting (strict spec compliance)
TOOL_NAME_SUGGESTIONS = {
    # Shell commands -> list_directory
    "ls": "list_directory",
    "dir": "list_directory",
    "list": "list_directory",
    
    # Shell commands -> shell (if available)
    "cat": "shell (cat file) or read_file",
    "rm": "shell",
    "mkdir": "shell",
    "cp": "shell",
    "mv": "shell",
    "grep": "shell",
    "find": "shell",
    "pwd": "shell",
    "cd": "shell",
    "echo": "shell",
    
    # File operations
    "read": "read_file",
    "write": "write_file",
    "load": "read_file",
    "save": "write_file",
    "open": "read_file",
    
    # Calculator/math
    "calc": "calculator",
    "math": "calculator",
    "calculate": "calculator",
    "compute": "calculator",
    "evaluate": "calculator",
    
    # Python
    "python": "python_repl",
    "repl": "python_repl",
    "exec": "python_repl",
    "run": "python_repl or shell",
    "execute": "python_repl or shell",
    
    # DateTime
    "date": "get_date",
    "time": "get_time",
    "datetime": "get_time or get_date",
    "now": "get_time",
    "today": "get_date",
    
    # Web
    "search": "web-search",
    "web": "web-search or http_get",
    "fetch": "http_get",
    "request": "http_get",
    
    # JSON
    "parse": "parse_json",
    "json": "parse_json",
    
    # Text
    "count": "count_words or count_chars",
    "words": "count_words",
    "chars": "count_chars",
}


def get_tool_suggestion(hallucinated_name: str, available_tools: list[str]) -> str | None:
    """
    Get a suggested tool name for a hallucinated tool name.
    
    OpenResponses Strict Compliance:
    This does NOT auto-correct the tool name. It only provides a hint
    for the model to correct itself on the next iteration.
    
    Args:
        hallucinated_name: The tool name the model used
        available_tools: List of actually available tool names
        
    Returns:
        Suggested tool name if found, None otherwise
    """
    name_lower = hallucinated_name.lower().replace("-", "_").replace(" ", "_")
    
    # Direct lookup
    if name_lower in TOOL_NAME_SUGGESTIONS:
        suggestion = TOOL_NAME_SUGGESTIONS[name_lower]
        # If suggestion contains available tool, return it
        for avail in available_tools:
            if avail in suggestion:
                return avail
        return suggestion
    
    # Partial match in suggestions
    for key, suggestion in TOOL_NAME_SUGGESTIONS.items():
        if key in name_lower or name_lower in key:
            for avail in available_tools:
                if avail in suggestion:
                    return avail
            return suggestion
    
    return None


# ============================================================================
# Tool Alternatives Mapping
# ============================================================================

# When a tool fails, suggest these alternatives
TOOL_ALTERNATIVES = {
    "calculator": ["python_repl"],
    "shell": ["python_repl"],
    "read_file": ["shell"],  # Can use cat via shell
    "write_file": ["shell"],  # Can use echo via shell
    "http_get": [],  # No direct alternative
    "python_repl": ["calculator"],
    "get_time": ["python_repl"],  # datetime in Python
    "get_date": ["python_repl"],
    "list_directory": ["shell"],
}


# ============================================================================
# Error Recovery State
# ============================================================================

@dataclass
class ToolFailureRecord:
    """Record of a single tool failure."""
    tool_name: str
    error_message: str
    step: int
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorRecoveryTracker:
    """
    Tracks error recovery state to prevent infinite loops.
    
    Tracks:
    - Consecutive failures per tool
    - Total failures across all tools
    - Failure history for analysis
    
    Features:
    - Suggests alternative tools after repeated failures
    - Provides tool-specific hints for common errors
    - Terminates loop when failure threshold exceeded
    """
    
    # Maximum consecutive failures for same tool before suggesting alternatives
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES
    
    # Maximum total failures before suggesting loop termination
    max_total_failures: int = DEFAULT_MAX_TOTAL_FAILURES
    
    # Track consecutive failures per tool: tool_name -> count
    consecutive_failures: dict[str, int] = field(default_factory=dict)
    
    # Track all failure records
    failure_history: list[ToolFailureRecord] = field(default_factory=list)
    
    # Track total failures in current run
    total_failures: int = 0
    
    # R06.52: consecutive steps in which EVERY tool call failed (any tool).
    # Used for termination so a single bad step never kills a healthy run.
    consecutive_all: int = 0
    
    # R06.52: recent failures keyed by (tool, args) signature -> count,
    # so the harness can block the model from re-issuing the exact same
    # failing call indefinitely (hallucinated-argument death spiral).
    recent_failures: dict[tuple, int] = field(default_factory=dict)
    
    # R06.52: how many times the identical call may fail before blocking
    max_identical_failures: int = DEFAULT_MAX_IDENTICAL_FAILURES
    
    def record_failure(
        self, 
        tool_name: str, 
        error_message: str, 
        step: int,
        arguments: dict[str, Any] | None = None
    ) -> None:
        """
        Record a tool failure.
        
        Args:
            tool_name: Name of the tool that failed
            error_message: Error message from the tool
            step: Current step number
            arguments: Arguments passed to the tool (optional)
        """
        # Increment consecutive failures for this tool
        self.consecutive_failures[tool_name] = self.consecutive_failures.get(tool_name, 0) + 1
        self.total_failures += 1
        
        # R06.52: track consecutive all-failure steps + identical-call counts
        self.consecutive_all += 1
        sig = self._signature(tool_name, arguments or {})
        self.recent_failures[sig] = self.recent_failures.get(sig, 0) + 1
        
        # Record in history
        self.failure_history.append(ToolFailureRecord(
            tool_name=tool_name,
            error_message=error_message,
            step=step,
            arguments=arguments or {}
        ))
    
    @staticmethod
    def _signature(tool_name: str, arguments: dict[str, Any]) -> tuple:
        """Build a hashable signature of a tool call (order-independent)."""
        try:
            frozen = json.dumps(arguments, sort_keys=True, default=str)
        except Exception:
            frozen = str(sorted((str(k), str(v)) for k, v in arguments.items()))
        return (tool_name, frozen)
    
    def get_repeat_failure(self, tool_name: str, arguments: dict[str, Any] | None = None) -> int:
        """
        Get how many times this exact call (tool + arguments) has failed.
        
        Args:
            tool_name: Name of the tool
            arguments: Arguments about to be passed (optional)
        """
        return self.recent_failures.get(self._signature(tool_name, arguments or {}), 0)
    
    def should_block_repeat(self, tool_name: str, arguments: dict[str, Any] | None = None) -> bool:
        """
        Check whether this exact call has already failed max_identical_failures
        times and should be blocked before execution (it will fail again).
        
        Args:
            tool_name: Name of the tool
            arguments: Arguments about to be passed (optional)
        """
        return self.get_repeat_failure(tool_name, arguments) >= self.max_identical_failures
    
    def format_repeat_block(self, tool_name: str, arguments: dict[str, Any] | None = None) -> str:
        """
        Build the observation message used when an identical failing call is
        blocked before execution. Instructs the model to change its approach.
        """
        count = self.get_repeat_failure(tool_name, arguments)
        args_str = ""
        try:
            args_str = json.dumps(arguments or {}, ensure_ascii=False)
        except Exception:
            args_str = str(arguments)
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
        return (
            f"Error: Repeated identical tool call blocked. The call "
            f"{tool_name}({args_str}) has already failed {count} times with the "
            f"same result. Do NOT repeat the same call. Change your approach: "
            f"fix the arguments, use a different tool, or continue without it."
        )
    
    def record_success(self, tool_name: str) -> None:
        """
        Record a tool success (resets consecutive failures for that tool).
        
        Args:
            tool_name: Name of the tool that succeeded
        """
        # Reset consecutive failures for this tool
        if tool_name in self.consecutive_failures:
            del self.consecutive_failures[tool_name]
        # R06.52: any success proves the run is not stuck — reset the
        # consecutive all-failure counter.
        self.consecutive_all = 0
    
    def get_consecutive_failures(self, tool_name: str) -> int:
        """Get the number of consecutive failures for a tool."""
        return self.consecutive_failures.get(tool_name, 0)
    
    def should_terminate(self) -> bool:
        """
        Check if the loop should terminate.
        
        R06.52 semantics: terminate only when EVERY tool call in each of the
        last ``max_total_failures`` consecutive steps failed (i.e. the run is
        genuinely stuck). A lifetime total would kill long healthy runs that
        legitimately accumulate scattered errors.
        """
        return self.consecutive_all >= self.max_total_failures
    
    @property
    def consecutive_all_failures(self) -> int:
        """Number of consecutive steps in which every tool call failed."""
        return self.consecutive_all
    
    def get_alternative_tools(self, tool_name: str) -> list[str]:
        """Get suggested alternative tools for a failing tool."""
        return TOOL_ALTERNATIVES.get(tool_name, [])
    
    def get_error_hint(self, tool_name: str, error_message: str) -> tuple[str, str] | None:
        """
        Get a tool-specific hint for an error.
        
        Args:
            tool_name: Name of the tool
            error_message: Error message from the tool
            
        Returns:
            Tuple of (hint, suggestion) or None if no specific hint
        """
        error_lower = error_message.lower()
        
        # Check tool-specific hints
        tool_hints = TOOL_ERROR_HINTS.get(tool_name, {})
        for pattern, (hint, suggestion) in tool_hints.items():
            if re.search(pattern, error_lower):
                return (hint, suggestion)
        
        # Check generic hints
        for pattern, (hint, suggestion) in GENERIC_ERROR_HINTS.items():
            if pattern in error_lower:
                return (hint, suggestion)
        
        return None
    
    def build_recovery_message(
        self, 
        tool_name: str, 
        error_message: str,
        available_tools: list[str]
    ) -> str:
        """
        Build a comprehensive recovery message for a tool failure.
        
        Args:
            tool_name: Name of the failing tool
            error_message: Error message from the tool
            available_tools: List of available tool names
            
        Returns:
            Formatted recovery message for the model
        """
        parts = []
        
        # Get consecutive failure count
        consecutive = self.get_consecutive_failures(tool_name)
        
        # Get tool-specific hint
        hint = self.get_error_hint(tool_name, error_message)
        
        # Build the message
        if consecutive >= self.max_consecutive_failures:
            # Suggest alternatives
            alternatives = self.get_alternative_tools(tool_name)
            available_alternatives = [t for t in alternatives if t in available_tools]
            
            parts.append(f"⚠️ Tool '{tool_name}' has failed {consecutive} times in a row.")
            
            if available_alternatives:
                parts.append(f"Consider using an alternative: {', '.join(available_alternatives)}")
            
            if hint:
                hint_text, suggestion = hint
                parts.append(f"Hint: {hint_text}")
                parts.append(f"Suggestion: {suggestion}")
            else:
                parts.append("Try a different approach or check the tool arguments.")
                
        elif consecutive >= 2:
            # Warning about repeated failures
            parts.append(f"⚠️ Tool '{tool_name}' has failed {consecutive} times.")
            
            if hint:
                hint_text, suggestion = hint
                parts.append(f"Hint: {hint_text}")
                parts.append(f"Suggestion: {suggestion}")
        else:
            # First failure - just provide hint
            if hint:
                hint_text, suggestion = hint
                parts.append(f"Hint: {hint_text}")
                parts.append(f"Suggestion: {suggestion}")
        
        return "\n".join(parts)
    
    def reset(self) -> None:
        """Reset all tracking state."""
        self.consecutive_failures.clear()
        self.failure_history.clear()
        self.total_failures = 0
        self.consecutive_all = 0
        self.recent_failures.clear()


# ============================================================================
# Observation Enhancement
# ============================================================================

def build_enhanced_observation(
    tool_name: str,
    result: str,
    tracker: ErrorRecoveryTracker,
    available_tools: list[str],
    is_error: bool = False,
    retry_on_error: bool = True,
    tool_args: dict | None = None,
) -> str:
    """
    Build an enhanced observation message with tool-specific hints.
    
    When retry_on_error is True and a tool fails, includes:
    - The previous attempt's arguments for context
    - An explicit retry instruction for the model
    - Per-tool retry count to prevent infinite loops
    
    Args:
        tool_name: Name of the tool that was executed
        result: Result string from the tool
        tracker: Error recovery tracker instance
        available_tools: List of available tool names
        is_error: Whether the result is an error
        retry_on_error: Whether to include retry instructions (default: True)
        tool_args: Previous tool call arguments (for retry context)
        
    Returns:
        Enhanced observation message with contextual guidance
    """
    # Start with the result
    if is_error:
        observation = f"Observation: {result}"
    else:
        observation = f"Observation: {result}"
    
    # Add guidance based on result type
    if is_error or result.lower().startswith("error"):
        # Record failure and build recovery message
        recovery_msg = tracker.build_recovery_message(
            tool_name, 
            result, 
            available_tools
        )
        
        if recovery_msg:
            observation = f"{observation}\n\n{recovery_msg}"
        else:
            # Generic error recovery
            observation = f"{observation}\n\nNote: Try a different approach. Check the tool arguments and try again."
        
        # ATLAS-inspired retry-with-error-feedback:
        # Include previous attempt context and explicit retry instruction
        if retry_on_error and tool_args:
            retry_count = tracker.get_consecutive_failures(tool_name)
            observation = f"{observation}\n\n--- Retry Context ---"
            observation = f"{observation}\nPrevious attempt: {tool_name}({json.dumps(tool_args, default=str)})"
            if retry_count >= 2:
                observation = f"{observation}\n⚠️ This tool has failed {retry_count} times. Consider using a different tool or approach."
            else:
                observation = f"{observation}\nPlease try again with corrected arguments."
    else:
        # Success - record it and prompt for Final Answer
        tracker.record_success(tool_name)
        
        # Check if this looks like a simple result that should end the loop
        if _is_simple_result(result, tool_name):
            observation = f"{observation}\n\nNow output: Final Answer: <the result>"
        else:
            # Complex result - model may need to process further
            observation = f"{observation}\n\nIf you have the answer, output: Final Answer: <your answer>\nIf you need more information, use another tool."
    
    return observation


def _is_simple_result(result: str, tool_name: str) -> bool:
    """
    Determine if a result is simple enough to immediately prompt for Final Answer.
    
    Simple results are:
    - Short numeric values
    - Date/time strings
    - Single-line results under 100 chars
    
    Complex results are:
    - File contents
    - Directory listings
    - Long outputs
    """
    # Simple tools that typically produce direct answers
    simple_tools = {"calculator", "get_time", "get_date", "count_words", "count_chars"}
    if tool_name in simple_tools:
        return True
    
    # Check result length
    if len(result) > 200:
        return False
    
    # Check if result is a simple value (number, short string)
    result_stripped = result.strip()
    
    # Numeric result
    if re.match(r'^-?\d+\.?\d*$', result_stripped):
        return True
    
    # Date/time format
    if re.match(r'^\d{4}-\d{2}-\d{2}', result_stripped):
        return True
    
    # Time format
    if re.match(r'^\d{2}:\d{2}', result_stripped):
        return True
    
    # Short single-line result
    if '\n' not in result_stripped and len(result_stripped) < 100:
        return True
    
    return False


# ============================================================================
# Utility Functions
# ============================================================================

def build_retry_context(
    tool_name: str,
    tool_args: dict | None,
    tracker: ErrorRecoveryTracker,
    max_tool_retries: int = DEFAULT_MAX_TOOL_RETRIES,
) -> str:
    """
    Build a retry context message for failed tool calls.
    
    This is used by the native tool calling path, where the tool result is
    already in memory via add_tool_result(). This function creates a follow-up
    user message with retry context without duplicating the full observation.
    
    Args:
        tool_name: Name of the tool that failed
        tool_args: Previous tool call arguments
        tracker: Error recovery tracker instance
        max_tool_retries: Maximum retries before giving up
    
    Returns:
        Retry context message string, or empty string if no retry needed
    """
    if not tool_args:
        return ""
    
    retry_count = tracker.get_consecutive_failures(tool_name)
    
    if retry_count >= max_tool_retries:
        return ""
    
    parts = ["--- Retry Context ---"]
    parts.append(f"Previous attempt: {tool_name}({json.dumps(tool_args, default=str)})")
    
    if retry_count >= 2:
        parts.append(f"\u26a0\ufe0f This tool has failed {retry_count} times. Consider using a different tool or approach.")
    else:
        parts.append("The tool returned an error. Please try again with corrected arguments.")
    
    return "\n".join(parts)


# R06.52: error detection is decided by the FIRST non-empty line of a tool
# result only. The previous implementation scanned the whole text for
# substrings like "error:" or "timeout", so healthy results that merely
# *mention* errors ("grep found no matches for 'error'...", a log excerpt
# containing "timeout") were misclassified as failures — which poisoned the
# recovery tracker and terminated otherwise healthy audit runs.
_ERROR_FIRST_LINE_RE = re.compile(
    r"^\s*(?:"
    r"error|"
    r"security error|"
    r"failed|"
    r"file not found|"
    r"permission denied|"
    r"command timed out|"
    r"traceback \(most recent call last\)|"
    r"\[error\]|"
    r"\[sandbox error\]|"
    r"\[sandbox\].*(?:timeout|timed out|failed|exited)|"
    r"\[exit code: (?P<code>[1-9][0-9]*)\]|"
    r"tool '(?P<tool>[^']+)' (?:was blocked|not in allowed_tools|not found)"
    r")",
    re.IGNORECASE,
)


def is_error_result(result: str) -> bool:
    """
    Check if a tool result indicates an error.
    
    R06.52: only the first non-empty line is inspected, via a strict pattern
    list of error prefixes. An empty / whitespace-only result is treated as
    an error (the tool produced nothing useful). Results that merely mention
    error-ish words later in the text are NOT errors.
    """
    if not result or not result.strip():
        return True
    first_line = result.strip().splitlines()[0]
    return bool(_ERROR_FIRST_LINE_RE.match(first_line))


def extract_error_type(error_message: str) -> str:
    """Extract a normalized error type from an error message."""
    error_lower = error_message.lower()
    
    # Common error patterns
    patterns = [
        (r"syntax", "syntax_error"),
        (r"not found", "not_found"),
        (r"permission denied", "permission_denied"),
        (r"timeout", "timeout"),
        (r"connection", "connection_error"),
        (r"blocked", "blocked"),
        (r"security", "security_error"),
        (r"invalid", "invalid_input"),
        (r"missing", "missing_argument"),
    ]
    
    for pattern, error_type in patterns:
        if re.search(pattern, error_lower):
            return error_type
    
    return "unknown_error"


__all__ = [
    # Classes
    "ErrorRecoveryTracker",
    "ToolFailureRecord",
    
    # Functions
    "build_enhanced_observation",
    "build_retry_context",
    "is_error_result",
    "extract_error_type",
    "get_tool_suggestion",
    
    # Constants
    "TOOL_ERROR_HINTS",
    "GENERIC_ERROR_HINTS",
    "TOOL_NAME_SUGGESTIONS",
    "TOOL_ALTERNATIVES",
    "DEFAULT_MAX_CONSECUTIVE_FAILURES",
    "DEFAULT_MAX_TOTAL_FAILURES",
    "DEFAULT_MAX_CONSECUTIVE_ALL_FAILURES",
    "DEFAULT_MAX_IDENTICAL_FAILURES",
    "DEFAULT_MAX_TOOL_RETRIES",
    "DEFAULT_RETRY_ON_ERROR",
]