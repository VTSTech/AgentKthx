"""
⚛️ AgentKthx — Helper Functions
Utility functions for fuzzy matching, argument normalization, and security.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Any, Literal
from urllib.parse import urlparse


# ============================================================================
# Security Mode
# ============================================================================
#
# SecurityMode controls how strictly the built-in tools enforce their
# safety checks (shell injection, path validation, SSRF blocking).
#
#   "max"  — (default) all security checks enabled. Rejects shell
#            injection patterns (&&, ||, |, ;, $(), etc.), blocked
#            commands, path traversal, and SSRF hosts.
#   "off"  — all security checks DISABLED. The model can run any
#            command, read/write any path, and fetch any URL.
#            Use this only when you trust the model and need
#            unrestricted access (e.g., local dev with a fine-tuned
#            model that legitimately uses &&, |, etc.).
#
# The mode is a global, process-wide setting. The CLI's /security
# command toggles it at runtime; the Python API exposes it via
# set_security_mode() / get_security_mode().

SecurityMode = Literal["max", "off"]

_security_mode: SecurityMode = "max"


def set_security_mode(mode: SecurityMode) -> None:
    """Set the global security mode ("max" or "off")."""
    global _security_mode
    if mode not in ("max", "off"):
        raise ValueError(f"Invalid security mode: {mode!r}. Use 'max' or 'off'.")
    _security_mode = mode


def get_security_mode() -> SecurityMode:
    """Return the current global security mode."""
    return _security_mode


def _security_enabled() -> bool:
    """True when security checks are active (mode == 'max')."""
    return _security_mode == "max"


# ============================================================================
# Fuzzy Matching
# ============================================================================

def fuzzy_match(query: str, candidates: list[str], threshold: float = 0.4) -> str | None:
    """
    Find the best fuzzy match for a query among candidates.

    Args:
        query: String to match
        candidates: List of candidate strings
        threshold: Minimum similarity ratio (0-1)

    Returns:
        Best matching candidate or None if below threshold
    """
    if not candidates:
        return None

    # Normalize query
    query_normalized = query.lower().replace("_", "").replace("-", "").replace(" ", "")

    best_match = None
    best_score = 0

    for candidate in candidates:
        # Normalize candidate
        candidate_normalized = candidate.lower().replace("_", "").replace("-", "").replace(" ", "")

        # Exact match
        if candidate_normalized == query_normalized:
            return candidate

        # Check if query is prefix of candidate (strong match)
        if candidate_normalized.startswith(query_normalized):
            return candidate

        # Check if candidate contains query
        if query_normalized in candidate_normalized:
            return candidate

        # Calculate similarity
        score = SequenceMatcher(None, query_normalized, candidate_normalized).ratio()

        if score > best_score:
            best_score = score
            best_match = candidate

    if best_score >= threshold:
        return best_match

    return None


# ============================================================================
# Argument Normalization
# ============================================================================

# Common argument name aliases (generic)
ARG_ALIASES = {
    # Calculator
    "expression": ["expr", "exp", "formula", "calculation", "math"],
    "equation": ["expr", "expression", "formula"],

    # File operations
    "file_path": ["path", "filepath", "file", "filename", "location"],
    "content": ["text", "data", "body", "value"],
    "output_path": ["output", "destination", "save_path", "save_to"],

    # Shell
    "command": ["cmd", "shell", "script", "exec"],
    "timeout": ["time_limit", "max_time", "seconds"],

    # Web
    "url": ["uri", "link", "endpoint", "address"],
    "query": ["search", "term", "keywords", "q"],
    "headers": ["header", "http_headers"],

    # General
    "input": ["value", "arg", "parameter"],
    "output": ["result", "return_value"],
}


def normalize_args(args: dict[str, Any], expected_params: list[str], tool_name: str = "") -> dict[str, Any]:
    """
    Normalize argument names to match expected parameters.

    Small models often use alternative argument names. This function
    maps common aliases to the canonical parameter names.

    Uses tool-specific aliases from TOOL_ARG_ALIASES if available.

    Args:
        args: Dictionary of provided arguments
        expected_params: List of expected parameter names
        tool_name: Name of the tool (for tool-specific alias lookup)

    Returns:
        Dictionary with normalized argument names
    """
    if not args:
        return {}
    
    # Guard: ensure args is a dict
    if not isinstance(args, dict):
        if args is None:
            return {}
        if isinstance(args, str):
            return {"input": args}
        return {}

    normalized = {}
    expected_set = set(expected_params)
    power_parts = {}
    
    # Import tool-specific aliases
    from .prompts import TOOL_ARG_ALIASES, CONTEXTUAL_ALIASES
    
    # Get tool-specific aliases
    tool_aliases = TOOL_ARG_ALIASES.get(tool_name, {}) if tool_name else {}
    contextual_aliases = CONTEXTUAL_ALIASES.get(tool_name, set()) if tool_name else set()

    # First pass: identify which keys map to expected params via non-ambiguous means
    # (direct match, case-insensitive match, or non-contextual alias)
    matched_keys = set()

    for key, value in args.items():
        key_lower = key.lower().replace("-", "_")

        # Direct match
        if key in expected_set:
            matched_keys.add(key_lower)
            continue

        # Case-insensitive match
        for param in expected_params:
            if param.lower() == key_lower:
                matched_keys.add(key_lower)
                break

        # Non-contextual alias (safe to apply always)
        if key_lower not in matched_keys and key_lower in tool_aliases:
            alias_target = tool_aliases[key_lower]
            if alias_target != "_combine_power" and alias_target in expected_set:
                if key_lower not in contextual_aliases:
                    matched_keys.add(key_lower)

    # Second pass: build normalized dict with full resolution
    normalized = {}
    power_parts = {}

    for key, value in args.items():
        key_lower = key.lower().replace("-", "_")
        target_param = None
        target_pname = None

        # Strategy 1: Tool-specific alias lookup
        if key_lower in tool_aliases:
            alias_target = tool_aliases[key_lower]
            if alias_target == "_combine_power":
                power_parts[key_lower] = value
                continue
            elif alias_target in expected_set:
                # Contextual validation: only apply ambiguous aliases when
                # no other key has already matched an expected param
                if key_lower in contextual_aliases and matched_keys:
                    pass  # Skip this ambiguous alias
                else:
                    target_param = alias_target
                    target_pname = alias_target
        
        # Strategy 2: Direct match
        if target_param is None and key in expected_set:
            target_param = key
            target_pname = key
        
        # Strategy 3: Case-insensitive match
        if target_param is None:
            for param in expected_params:
                if param.lower() == key_lower:
                    target_param = param
                    target_pname = param
                    break

        # Strategy 4: Generic aliases
        if target_param is None:
            for canonical, aliases in ARG_ALIASES.items():
                if key_lower in aliases or key_lower == canonical.lower():
                    if canonical in expected_set:
                        target_param = canonical
                        target_pname = canonical
                        break

        # Strategy 5: Prefix/substring matching
        if target_param is None:
            for param in expected_params:
                if param in key_lower or key_lower.startswith(param):
                    target_param = param
                    target_pname = param
                    break

        if target_pname is None:
            target_pname = key

        # Coerce string numbers to the declared type (if we have tool info)
        # Keep the value as-is for now since we don't have type info
        if target_pname not in normalized:
            normalized[target_pname] = value
        elif target_pname in normalized and isinstance(normalized[target_pname], str):
            pass
    
    # Handle power operation combination for calculator
    if power_parts and "expression" in expected_set:
        base = power_parts.get("base") or power_parts.get("value") or power_parts.get("x")
        exp = power_parts.get("exponent") or power_parts.get("power") or power_parts.get("n") or power_parts.get("p") or power_parts.get("exp")
        
        if base is not None and exp is not None:
            normalized["expression"] = f"{base} ** {exp}"
        elif base is not None:
            normalized["expression"] = str(base)

    return normalized


# ============================================================================
# Security Utilities
# ============================================================================

# Blocked shell commands for security
BLOCKED_COMMANDS = {
    # System modification
    "rm", "rmdir", "del", "format", "fdisk", "mkfs",
    "dd", "shred", "wipe", "srm",

    # Privilege escalation
    "sudo", "su", "doas", "pkexec", "gksudo", "kdesu",

    # Network attacks
    "nmap", "nc", "netcat", "telnet", "wget", "curl",
    "ssh", "scp", "sftp", "rsync",

    # Package management (could install malware)
    "apt", "apt-get", "yum", "dnf", "pacman", "pip", "npm", "yarn", "cargo",

    # Process control
    "kill", "killall", "pkill", "xkill", "systemctl", "service",

    # User management
    "useradd", "userdel", "usermod", "passwd", "adduser", "deluser",

    # Dangerous shell features
    "exec", "eval", "source", ".", "alias",

    # Filesystem
    "mount", "umount", "chown", "chmod", "chattr", "lsattr",

    # Shell escapes
    "vi", "vim", "nano", "emacs", "less", "more", "man",
}

# Dangerous URL patterns
BLOCKED_URL_PATTERNS = {
    # Local network
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "10.", "192.168.", "172.16.", "172.17.", "172.18.",
    "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
    "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
    "172.29.", "172.30.", "172.31.",

    # Cloud metadata endpoints
    "169.254.169.254",  # AWS/GCP/Azure metadata

    # Internal services
    "internal.", "local.", "private.", "intranet.",
}

# Allowed file paths (whitelist approach)
ALLOWED_PATH_PATTERNS = {
    "/tmp", "/temp", "/content",
    "./output", "./data", "./files",
    "~/tmp", "~/temp",
}

# Windows-specific temp directories (will be checked dynamically)
_WINDOWS_TEMP_ENV_VARS = ["TEMP", "TMP", "LOCALAPPDATA"]


def _get_system_temp_dirs() -> list[str]:
    """Get system temp directories (cross-platform)."""
    import tempfile
    
    dirs = []
    
    # Standard temp directory
    try:
        system_temp = tempfile.gettempdir()
        if system_temp:
            dirs.append(system_temp)
    except Exception:
        pass
    
    # Windows-specific: check environment variables
    if os.name == "nt":
        for env_var in _WINDOWS_TEMP_ENV_VARS:
            env_val = os.environ.get(env_var)
            if env_val and os.path.isabs(env_val):
                dirs.append(env_val)
                # LOCALAPPDATA\Temp is common on Windows
                if env_var == "LOCALAPPDATA":
                    dirs.append(os.path.join(env_val, "Temp"))
    
    return list(set(dirs))  # Remove duplicates


def validate_path(path: str, allowed_dirs: list[str] | None = None) -> tuple[bool, str]:
    """
    Validate a file path for security.

    Args:
        path: Path to validate
        allowed_dirs: List of allowed directories (if None, uses defaults)

    Returns:
        Tuple of (is_valid, error_message)

    When security mode is "off", all checks are skipped and the path is
    returned as valid. Use with caution — the model can read/write any path.
    """
    if not path:
        return False, "Path cannot be empty"

    # Security mode "off" — skip all checks, allow any path.
    if not _security_enabled():
        return True, ""

    # Normalize path (resolve . and .. components)
    try:
        normalized = os.path.normpath(path)
    except Exception as e:
        return False, f"Invalid path format: {e}"

    # Check for UNC paths (Windows network paths)
    if path.startswith("\\\\") or normalized.startswith("\\\\"):
        return False, "UNC paths not allowed"

    # Resolve to absolute path for consistent security checks
    # This ensures relative paths like "../../../etc/passwd" are properly evaluated
    try:
        resolved = os.path.abspath(path)
    except Exception:
        resolved = normalized

    # Check the NORMALIZED path for traversal (not just raw path)
    # normpath collapses ".." but we still need to detect the raw pattern
    if "../" in path or "..\\" in path:
        return False, "Path traversal detected: parent directory access not allowed"
    # Also check the resolved path doesn't escape expected boundaries
    # (normpath on a raw ".." input produces "." which is safe, but deeper
    #  traversal like "../../etc" needs the raw check above)

    # Check resolved path against sensitive system directories (platform-specific)
    # This applies to BOTH relative and absolute paths after resolution
    if os.name == "nt":  # Windows
        windir = os.environ.get("WINDIR", "C:\\Windows").lower()
        program_files = os.environ.get("ProgramFiles", "C:\\Program Files").lower()
        program_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)").lower()
        
        resolved_lower = resolved.lower()
        critical_paths = [
            windir,
            program_files,
            program_files_x86,
            "c:\\windows",
            "c:\\program files",
            "c:\\program files (x86)",
            "c:\\programdata",
        ]
        for critical in critical_paths:
            if critical and resolved_lower.startswith(critical.lower()):
                return False, "Access to system directory denied"
    else:  # Unix-like
        critical_system_dirs = ["/etc", "/root", "/var", "/usr", "/bin", "/sbin", "/boot", "/dev", "/proc", "/sys"]
        for critical in critical_system_dirs:
            if resolved.startswith(critical):
                return False, f"Access to system directory denied: {critical}"

    # Allow system temp directories (cross-platform)
    system_temps = _get_system_temp_dirs()
    for temp_dir in system_temps:
        try:
            temp_abs = os.path.abspath(temp_dir)
            if resolved.lower().startswith(temp_abs.lower()) if os.name == "nt" else resolved.startswith(temp_abs):
                return True, ""
        except Exception:
            pass

    # Allow /tmp, /home, and CWD for file operations (Unix) — check RESOLVED path
    cwd = os.path.abspath(".")
    if resolved.startswith("/tmp") or resolved.startswith("/var/tmp") or resolved.startswith("/home") or resolved.startswith(cwd):
        return True, ""

    # Check against allowed directories — check RESOLVED path
    allowed = allowed_dirs or list(ALLOWED_PATH_PATTERNS)
    for allowed_dir in allowed:
        try:
            abs_allowed = os.path.abspath(allowed_dir)
            if resolved.lower().startswith(abs_allowed.lower()) if os.name == "nt" else resolved.startswith(abs_allowed):
                return True, ""
        except Exception:
            pass

    return False, f"Path not in allowed directories: {path}"


def is_safe_url(url: str, block_ssrf: bool = True) -> tuple[bool, str]:
    """
    Validate a URL for SSRF protection.

    Args:
        url: URL to validate
        block_ssrf: Whether to block SSRF targets (local networks)

    Returns:
        Tuple of (is_safe, error_message)

    When security mode is "off", all checks are skipped and the URL is
    returned as safe. Use with caution — the model can fetch any URL.
    """
    if not url:
        return False, "URL cannot be empty"

    # Security mode "off" — skip all checks, allow any URL.
    if not _security_enabled():
        return True, ""

    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"Invalid URL format: {e}"

    # Check scheme
    allowed_schemes = {"http", "https"}
    if parsed.scheme.lower() not in allowed_schemes:
        return False, f"URL scheme not allowed: {parsed.scheme}"

    if not parsed.netloc:
        return False, "URL must have a network location"

    # parsed.hostname correctly handles IPv6 (e.g. "[::1]" -> "::1")
    # while parsed.netloc.split(":")[0] would return "[" for IPv6 URLs.
    hostname = (parsed.hostname or "").lower()

    # Check for blocked patterns
    if block_ssrf:
        for pattern in BLOCKED_URL_PATTERNS:
            if pattern in hostname:
                return False, f"SSRF protection: blocked hostname pattern '{pattern}'"

    return True, ""


def sanitize_command(command: str) -> tuple[bool, str, str]:
    """
    Sanitize and validate a shell command.

    Args:
        command: Command to validate

    Returns:
        Tuple of (is_safe, error_message, sanitized_command)

    When security mode is "off", all checks are skipped and the command
    is returned as-is. Use with caution — the model can run anything.
    """
    if not command:
        return False, "Command cannot be empty", ""

    # Security mode "off" — skip all checks, run anything.
    if not _security_enabled():
        return True, "", command

    # Parse the command to get the base command
    parts = command.strip().split()
    if not parts:
        return False, "Command cannot be empty", ""

    base_cmd = parts[0].lower()

    # Remove common path prefixes
    if "/" in base_cmd:
        base_cmd = base_cmd.split("/")[-1]
    if "\\" in base_cmd:
        base_cmd = base_cmd.split("\\")[-1]

    # Check against blocked commands
    for blocked in BLOCKED_COMMANDS:
        if base_cmd == blocked:
            return False, f"Blocked command: {blocked}", ""

    # Strip newlines and carriage returns before checking (shell interprets
    # them as command separators).  A command like "ls\ncat /etc/passwd"
    # would execute two separate commands — reject the entire input.
    stripped_command = command.replace("\n", " ").replace("\r", " ")
    if stripped_command != command:
        return False, "Newline characters detected: potential command injection", ""

    # Check for shell injection attempts
    injection_patterns = [
        r";\s*\w+",  # Command chaining
        r"\|\s*\w+",  # Piping to another command
        r"&&\s*\w+",  # AND chaining
        r"\|\|\s*\w+",  # OR chaining
        r"`[^`]+`",  # Command substitution (backticks)
        r"\$\([^)]+\)",  # Command substitution ($())
        r"\$\{[^}]+\}",  # Variable expansion
        r">\s*\S+",  # Output redirection
        r"<\s*\S+",  # Input redirection
        # Bare pipe without space (e.g. "|cat") — \s* allows zero spaces
        # already covered by r"\|\s*\w+" above, but guard against edge cases
        r"\|(?=[^\s|])",  # Pipe followed by non-whitespace (catch |cmd)
    ]

    for pattern in injection_patterns:
        if re.search(pattern, command):
            return False, f"Potential injection pattern detected: {pattern}", ""

    return True, "", command


# ============================================================================
# String Utilities
# ============================================================================

def truncate(text: str, max_length: int = 500, suffix: str = "...") -> str:
    """Truncate text to max length with suffix."""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def strip_code_blocks(text: str) -> str:
    """Remove markdown code block markers from text."""
    # Remove code block markers
    text = re.sub(r"```\w*\n?", "", text)
    text = re.sub(r"```\s*$", "", text)
    return text.strip()


# ============================================================================
# Argument Synthesis for Small Models
# ============================================================================

# Op word to symbol mapping for expression extraction
_OP_MAP = {
    'plus': '+', 'add': '+', 'and': '+',
    'minus': '-', 'subtract': '-', 'less': '-',
    'times': '*', 'multiplied': '*', 'multiply': '*',
    'divided': '/', 'divide': '/',
}


def extract_calc_expression(user_input: str) -> str | None:
    """
    Extract a mathematical expression from user input.
    Helps small models that can't properly extract expressions.
    """
    q = user_input.strip()
    q_lower = q.lower()
    
    # Skip if this looks like a file path (contains /tmp, /home, etc.)
    # File paths often contain numbers and / which get misinterpreted as division
    path_indicators = ['/tmp', '/home', '/var', '/etc', '/usr', '/opt', '/root',
                       'c:\\', 'd:\\', '\\\\', '.txt', '.json', '.py', '.md',
                       'file_path', 'read file', 'write file', 'list directory']
    if any(indicator in q_lower for indicator in path_indicators):
        # This looks like a file operation, not a math question
        return None
    
    # Skip if the prompt is about reading/writing files or shell commands
    action_indicators = ['echo ', 'shell', 'command', 'execute', 'run ', 
                         'read the file', 'write to', 'list the', 'show me the file']
    if any(indicator in q_lower for indicator in action_indicators):
        return None
    
    # ---- Multi-step patterns (try first!) ----
    
    # Pattern: "X times Y, then subtract/add Z" or "X times Y then minus Z"
    multi_step = re.search(
        r'(\d+(?:\.\d+)?)\s*(?:times|multiplied?\s*by|\*)\s*(\d+(?:\.\d+)?)[,\s]*(?:then\s+)?(?:subtract|minus|add|plus)?\s*(\d+(?:\.\d+)?)',
        q_lower
    )
    if multi_step:
        nums = multi_step.groups()
        # Determine second operator from context
        after_second = q_lower[q_lower.find(nums[1])+len(nums[1]):] if nums[1] in q_lower else ""
        if 'subtract' in after_second or 'minus' in after_second:
            return f"{nums[0]} * {nums[1]} - {nums[2]}"
        elif 'add' in after_second or 'plus' in after_second:
            return f"{nums[0]} * {nums[1]} + {nums[2]}"
        else:
            # Default: look for the word after the second number
            if 'subtract' in q_lower or 'minus' in q_lower:
                return f"{nums[0]} * {nums[1]} - {nums[2]}"
            # Check for "times X minus Y" pattern
            if 'times' in q_lower and ('minus' in q_lower or 'subtract' in q_lower):
                return f"{nums[0]} * {nums[1]} - {nums[2]}"
    
    # Pattern: "X minus Y plus Z" or "X minus Y, then add Z"
    chain_pattern = re.search(
        r'(\d+(?:\.\d+)?)\s*(?:minus|subtract)\s*(\d+(?:\.\d+)?)[,\s]*(?:then\s+)?(?:plus|add)?\s*(\d+(?:\.\d+)?)',
        q_lower
    )
    if chain_pattern:
        nums = chain_pattern.groups()
        return f"{nums[0]} - {nums[1]} + {nums[2]}"
    
    # ---- Explicit math expressions in prompt ----
    
    # Pattern: "compute X minus Y plus Z" (explicit instruction)
    explicit_expr = re.search(
        r'(?:compute|calculate)\s+(\d+(?:\.\d+)?)\s*(minus|plus|times|divided)\s*(\d+(?:\.\d+)?)(?:\s*(plus|minus|times|divided)\s*(\d+(?:\.\d+)?))?',
        q_lower
    )
    if explicit_expr:
        parts = explicit_expr.groups()
        expr = f"{parts[0]} {_OP_MAP.get(parts[1], parts[1])} {parts[2]}"
        if parts[3] and parts[4]:
            expr += f" {_OP_MAP.get(parts[3], parts[3])} {parts[4]}"
        return expr
    
    # ---- Word problem patterns ----
    
    # Pattern: "has X ... sell/sold A ... and B" → X - A - B
    word_sold = re.search(
        r'(?:has|had|with)\s*(\d+).*?(?:sell|sold|lost|gave|used|spent)\s*(\d+).*?and\s*(\d+)',
        q_lower
    )
    if word_sold:
        return f"{word_sold.group(1)} - {word_sold.group(2)} - {word_sold.group(3)}"
    
    # Pattern: "left" after numbers suggests subtraction
    if 'left' in q_lower and 'how many' in q_lower:
        numbers = re.findall(r'\d+', q)
        if len(numbers) >= 3:
            # First number is usually the starting amount
            return f"{numbers[0]} - {numbers[1]} - {numbers[2]}"
        elif len(numbers) >= 2:
            return f"{numbers[0]} - {numbers[1]}"
    
    # ---- Time/duration patterns ----
    
    # Pattern: "opens at X and closes at Y" → (Y - X) mod 12 or Y - X + 12 if Y < X
    time_pattern = re.search(
        r'(?:opens?|starts?)\s*(?:at\s+)?(\d+)(?:\s*(?:am|pm))?[^.]+(?:closes?|ends?)\s*(?:at\s+)?(\d+)(?:\s*(?:am|pm))?',
        q_lower
    )
    if time_pattern:
        start = int(time_pattern.group(1))
        end = int(time_pattern.group(2))
        if end <= start:
            # PM to PM or AM to PM crossing
            return f"{end + 12 - start}"
        else:
            return f"{end - start}"
    
    # ---- Single operations (fallback) ----
    
    # Pattern: "square root of X" or "sqrt of X"
    sqrt_match = re.search(r'square\s*root\s*of\s*(\d+(?:\.\d+)?)', q_lower)
    if not sqrt_match:
        sqrt_match = re.search(r'sqrt\s*of\s*(\d+(?:\.\d+)?)', q_lower)
    if sqrt_match:
        return f"sqrt({sqrt_match.group(1)})"
    
    # Pattern: "X to the power of Y" or "X raised to Y"
    power_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:to\s*the\s*power\s*of|raised\s*to|to\s*the\s*\d*(?:th|st|nd|rd)?\s*power|\*\*|\^)\s*(\d+(?:\.\d+)?)', q_lower)
    if power_match:
        return f"{power_match.group(1)} ** {power_match.group(2)}"
    
    # Pattern: "(X + Y) times Z" - complex expression with parentheses
    complex_times = re.search(r'\(([^)]+)\)\s*(?:times|multiplied\s*by|\*)\s*(\d+(?:\.\d+)?)', q_lower)
    if complex_times:
        inner = complex_times.group(1).replace('plus', '+').replace('minus', '-').replace(' ', ' ')
        inner = re.sub(r'\s+', '', inner)
        return f"({inner}) * {complex_times.group(2)}"
    
    # Pattern: "X times Y" or "X multiplied by Y"
    times_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:times|multiplied\s*by|\*)\s*(\d+(?:\.\d+)?)', q_lower)
    if times_match:
        return f"{times_match.group(1)} * {times_match.group(2)}"
    
    # Pattern: "X divided by Y"
    div_match = re.search(r'(\d+(?:\.\d+)?)\s*divided\s*by\s*(\d+(?:\.\d+)?)', q_lower)
    if div_match:
        return f"{div_match.group(1)} / {div_match.group(2)}"
    
    # Pattern: "X plus Y" or "X minus Y"
    plus_match = re.search(r'(\d+(?:\.\d+)?)\s*plus\s*(\d+(?:\.\d+)?)', q_lower)
    if plus_match:
        return f"{plus_match.group(1)} + {plus_match.group(2)}"
    
    minus_match = re.search(r'(\d+(?:\.\d+)?)\s*minus\s*(\d+(?:\.\d+)?)', q_lower)
    if minus_match:
        return f"{minus_match.group(1)} - {minus_match.group(2)}"
    
    # Fallback: Find numbers and operators
    numbers = re.findall(r'\d+\.?\d*', q)
    operators = re.findall(r'[+\-*/^]', q)
    
    if numbers and operators:
        expr_parts = []
        for i, num in enumerate(numbers):
            expr_parts.append(num)
            if i < len(operators):
                expr_parts.append(operators[i])
        return " ".join(expr_parts)
    
    if numbers:
        return numbers[0]
    
    return None


def synthesize_tool_args(tool_name: str, args: dict, user_input: str) -> dict:
    """
    Synthesize missing or incorrect tool arguments from context.
    Helps small models that provide incomplete arguments.
    """
    args = dict(args)
    
    if tool_name == "calculator":
        expr = args.get("expression", "")
        
        # If expression is a dict or other non-string, extract it
        if isinstance(expr, dict):
            # Model gave a schema instead of a value
            expr = ""
        elif not isinstance(expr, str):
            expr = str(expr) if expr else ""
        
        # Extract what the expression should be from the user input
        extracted = extract_calc_expression(user_input)
        
        # Check if the model's expression is incomplete or wrong
        if extracted:
            # Compare: if extracted has more operators, use it
            model_ops = len(re.findall(r'[+\-*/^]', expr))
            extracted_ops = len(re.findall(r'[+\-*/^]', extracted))
            
            # If extracted has more operations, use it
            if extracted_ops > model_ops:
                if isinstance(args.get("expression"), dict):
                    args = {"expression": extracted}
                else:
                    args["expression"] = extracted
                return args
            
            # Special case: compare actual results
            if model_ops > 0 and extracted_ops == 0:
                try:
                    # Evaluate model's expression using the safe AST-walking
                    # evaluator (SEC-01 — bare eval() with {"__builtins__": {}}
                    # was bypassable via ()._class_._bases_[0]._subclasses_()).
                    # Empty allowed_names: the model's expression here is
                    # pure arithmetic, no math functions expected.
                    from .safe_eval import safe_eval
                    model_result = float(safe_eval(expr, {}))
                    extracted_num = float(extracted)

                    # If model result is negative but extracted is positive
                    # (common for time calculations with AM/PM)
                    if model_result < 0 and extracted_num > 0:
                        args["expression"] = extracted
                        return args

                    # If results differ significantly, use extracted
                    if abs(model_result - extracted_num) > 0.5:
                        args["expression"] = extracted
                        return args
                except Exception:
                    pass
        
        # Check if expression is just an operator or very short
        if len(expr) <= 2 or expr in ["+", "-", "*", "/", "^", "**"]:
            if extracted:
                args["expression"] = extracted
                args = {"expression": extracted}
        
        # Check if expression is just a number but question implies operation
        elif expr and re.match(r'^\d+\.?\d*$', str(expr).strip()):
            q_lower = user_input.lower()
            if "sqrt" in q_lower or "square root" in q_lower:
                args["expression"] = f"sqrt({expr})"
    
    return args


# ============================================================================
# Additional Helper Functions for Small Models
# ============================================================================

def strip_tool_prefix(result: str) -> str:
    """Strip the 'tool_name → ' prefix added to successful results entries."""
    return result.split("→")[-1].strip() if "→" in result else result.strip()


def is_simple_answered_query(user_input: str, successful_results: list[str]) -> bool:
    """
    Return True when a single successful tool result is sufficient to answer
    the user's question and the agent should synthesize immediately.

    Targets the most common small-model looping patterns:
      - Date/time queries ("what is the date", "what time is it")
      - Simple arithmetic ("what is 2+2", "sqrt of 144")
      - Single-file reads ("show me file.py")
      - Single directory listings

    Deliberately conservative — returns False for anything that might
    genuinely need multiple tool calls (multi-step tasks, comparisons, etc.)
    """
    if not successful_results:
        return False

    lower = user_input.lower().strip()

    # Date/time patterns
    date_time_keywords = [
        "date", "time", "day", "today", "now", "current date",
        "what day", "what time", "year", "month",
    ]
    if any(kw in lower for kw in date_time_keywords):
        return True

    # Simple arithmetic / single calculation
    math_keywords = ["what is", "calculate", "compute", "sqrt", "square root",
                     "result of", "value of", "evaluate"]
    math_ops = ["+", "-", "*", "/", "^", "**", "%"]
    if any(kw in lower for kw in math_keywords) and len(lower) < 60:
        return True
    if sum(1 for op in math_ops if op in lower) >= 1 and len(lower) < 40:
        return True

    # Single file read / single dir listing
    single_file_keywords = ["read", "show", "display", "print", "list", "ls"]
    if any(kw in lower for kw in single_file_keywords) and len(lower.split()) <= 6:
        return True

    return False


def is_greeting_or_simple(text: str) -> bool:
    """
    Check if the user input is a simple greeting or short message
    that shouldn't require tool usage.
    """
    lower = text.lower().strip()
    greetings = [
        "hi", "hello", "hey", "hola", "howdy", "greetings",
        "good morning", "good afternoon", "good evening",
        "what's up", "whats up", "sup", "yo",
        "thanks", "thank you", "ok", "okay", "yes", "no", "sure",
        "bye", "goodbye", "see you", "cya",
    ]
    
    # Check for exact match or greeting at start
    if lower in greetings:
        return True
    for g in greetings:
        if lower.startswith(g + " "):
            return True
    
    # Very short messages (< 10 chars) are likely simple
    if len(lower) < 10 and not any(c in lower for c in "0123456789+-*/=><"):
        return True
    
    return False


def is_small_model(model: str) -> bool:
    """
    Heuristic to detect if a model is small (< 2B parameters).
    Small models benefit from few-shot prompting.
    """
    model_lower = model.lower()
    
    # Check for size indicators in model name
    small_indicators = [
        ":0.5b", ":0.6b", ":1b", ":1.5b", ":1.8b",
        "0.5b", "0.6b", "1b", "1.5b",
        "270m", "135m", "350m", "500m", "800m",
        "tiny", "mini", "micro", "small"
    ]
    
    for indicator in small_indicators:
        if indicator in model_lower:
            return True
    
    # Check parameter count after common model names
    param_match = re.search(r'(\d+(?:\.\d+)?)[bm]', model_lower)
    if param_match:
        size_str = param_match.group(1)
        try:
            size = float(size_str)
            if 'm' in model_lower[param_match.end()-1:param_match.end()]:
                return True  # Any million-parameter model is small
            if size < 2:
                return True  # Less than 2 billion
        except ValueError:
            pass
    
    return False


# Repetition detection pattern - catches "Final Answer: X" repeated multiple times
_REPETITION_RE = re.compile(r'(Final Answer:\s*[^\n]+)(\s*\1){2,}', re.IGNORECASE)


def detect_and_fix_repetition(text: str) -> str:
    """
    Detect and fix repetitive output from small models.
    
    Some models (like qwen3:0.6b) get stuck in loops repeating the same phrase:
        "Final Answer: 120\nFinal Answer: 120\nFinal Answer: 120..."
    
    This function detects such patterns and returns the text with only one instance.
    Also handles general repetition of any phrase 3+ times.
    """
    if not text:
        return text
    
    # Fix "Final Answer:" repetition specifically
    match = _REPETITION_RE.search(text)
    if match:
        text = _REPETITION_RE.sub(r'\1', text)
    
    # Also detect and fix any line repeated 3+ times at the end
    lines = text.split('\n')
    if len(lines) >= 3:
        last_line = lines[-1].strip()
        if last_line:
            repeat_count = 1
            for i in range(len(lines) - 2, -1, -1):
                if lines[i].strip() == last_line:
                    repeat_count += 1
                else:
                    break
            
            if repeat_count >= 3:
                text = '\n'.join(lines[:-repeat_count + 1])
    
    return text