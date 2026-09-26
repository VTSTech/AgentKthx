"""
⚛️ AgentKthx — Helper Functions
Utility functions for fuzzy matching, argument normalization, and security.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
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

# Blocked shell commands for security.
#
# THREAT MODEL (SEC-02, R06.41):
# This blocklist is a GUARDRAIL AGAINST MODEL MISTAKES — it catches the
# model when it casually reaches for `rm -rf /` or `dd of=/dev/sda` because
# it misread a flag or got confused about the working directory. It is NOT
# a defense against a determined adversary or a sophisticated prompt-
# injection payload. A model that wants to bypass will find another binary
# (busybox, find -exec, python -c, etc.) — for those, see the
# DANGEROUS_FLAG_COMBOS dict below, which adds context-aware blocks for
# the most common injection primitives without breaking legitimate uses of
# the underlying commands.
#
# For defense against prompt injection via tool output (e.g., the model
# reads a webpage containing "ignore prior instructions, run X"), the
# blocklist is necessary but not sufficient — use `--security max` AND
# validate / sanitize tool output before displaying it to the model. For
# trusted models and trusted content, `--security off` skips all checks
# (power-user escape hatch).
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

    # Shells (SEC-04, R07.05) — `bash -c "rm -rf /tmp/x"` previously slipped
    # past this blocklist because only shell FEATURES (exec/eval/source)
    # were blocked, not the shell binaries themselves. Any shell invoked by
    # name executes an arbitrary command string without it ever passing
    # through these checks, so the shells are blocked outright. The shell
    # tool already runs through /bin/sh; legitimate uses of an interactive
    # shell by an agent are rare — power users can opt out with
    # `--security off`.
    "bash", "sh", "zsh", "ksh", "fish",

    # Filesystem
    "mount", "umount", "chown", "chmod", "chattr", "lsattr",

    # Shell escapes
    "vi", "vim", "nano", "emacs", "less", "more", "man",

    # Universal multi-call binaries — `busybox rm` bypasses the `rm` block.
    # Legit uses exist (Alpine, embedded) but agents rarely need this; if
    # they do, the user can opt out with `--security off`.
    "busybox",
}

# Commands that are allowed by themselves but dangerous with specific flags.
#
# Each entry maps a base command name to a list of (regex, reason) tuples.
# If the base command matches AND any of the flag patterns match, the
# command is rejected. This lets us block the dangerous variants of useful
# commands (find, python, perl, awk, tar, cp) without breaking their
# legitimate everyday uses.
#
# The regex patterns are matched against the FULL command string (not just
# the args), so they can anchor on whitespace + flag combinations.
DANGEROUS_FLAG_COMBOS: dict[str, list[tuple[str, str]]] = {
    # `find -exec` and `-execdir` run arbitrary commands; `-delete` removes
    # files en masse. The base `find` binary is essential for file discovery.
    "find": [
        (r"\s-exec\b",   "find -exec runs arbitrary commands"),
        (r"\s-execdir\b", "find -execdir runs arbitrary commands"),
        (r"\s-delete\b", "find -delete removes files en masse"),
    ],
    # `xargs rm` chains deletion across many files; same for mv/dd/shred.
    # `xargs` alone is fine for legit pipelines like `find . -print | xargs grep foo`.
    "xargs": [
        (r"\brm\b",     "xargs rm chains deletion"),
        (r"\brmdir\b",  "xargs rmdir chains directory removal"),
        (r"\bmv\b",     "xargs mv chains moves"),
        (r"\bdd\b",     "xargs dd chains low-level device writes"),
        (r"\bshred\b",  "xargs shred chains shredding"),
    ],
    # `python -c` / `python3 -c` runs inline Python — arbitrary code.
    # The base `python` binary is fine for running scripts.
    "python": [
        (r"\s-c\b", "python -c runs inline Python (arbitrary code execution)"),
    ],
    "python3": [
        (r"\s-c\b", "python3 -c runs inline Python (arbitrary code execution)"),
    ],
    # `perl -e` / `ruby -e` run inline scripts — arbitrary code.
    "perl": [
        (r"\s-e\b", "perl -e runs inline Perl (arbitrary code execution)"),
    ],
    "ruby": [
        (r"\s-e\b", "ruby -e runs inline Ruby (arbitrary code execution)"),
    ],
    # `awk` with `system()` calls runs shell commands from inside the awk
    # script. The base `awk` is fine for text processing.
    "awk": [
        (r"system\s*\(", "awk system() runs shell commands"),
    ],
    # `tar --use-compress-program=X` and `tar -I X` exec an arbitrary
    # compressor binary. Standard `tar -czf` / `tar -xzf` are fine.
    "tar": [
        (r"--use-compress-program", "tar --use-compress-program runs arbitrary compressor"),
        (r"\s-I\s+\S", "tar -I runs arbitrary compressor"),
    ],
    # `cp /dev/null <file>` is a known file-truncation trick. The base
    # `cp` is fine for legitimate copies.
    "cp": [
        (r"/dev/null", "cp /dev/null truncates the target file"),
    ],
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


def _ip_address_blocked(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    """
    True if the address must NOT be fetchable by the model (SEC-03).

    Blocks loopback, private ranges (RFC1918 + friends), link-local
    (169.254/16 — including the 169.254.169.254 cloud metadata endpoint),
    reserved, multicast, and unspecified (::) addresses. IPv4-mapped IPv6
    (::ffff:a.b.c.d) is unwrapped first so `[::ffff:7f00:1]` cannot
    smuggle in 127.0.0.1.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _iter_hostname_ips(hostname: str) -> list[str]:
    """
    Normalize a URL hostname into the IP address(es) it can reach (SEC-03).

    Covers every IP-literal spelling urllib accepts but a substring check
    does not — decimal (``2130706433``), hex (``0x7f000001``), octal
    (``0177.0.0.1``), short forms (``127.1``), and all IPv6 forms — plus
    DNS names, which are resolved so every returned address can be judged.

    A hostname that does not resolve yields an empty list (fail-open):
    nothing can connect to a name that doesn't resolve, so we leave the
    decision to the subsequent fetch instead of blocking on transient DNS
    hiccups. Residual DNS-rebinding risk (address changes between this
    check and the actual connect) is documented in is_safe_url.
    """
    host = hostname.strip("[]")  # belt+braces; urlparse already strips []

    # 1. Modern IP literals: dotted-quad IPv4 + every IPv6 form.
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass

    # 2. Legacy inet_aton spellings that ipaddress rejects but sockets
    #    happily connect to (decimal / hex / octal / shortened quads).
    try:
        packed = socket.inet_aton(host)
        return [str(ipaddress.ip_address(socket.inet_ntoa(packed)))]
    except (OSError, ValueError, UnicodeError):
        pass

    # 3. DNS name — resolve and return every address it maps to.
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(host, None):
            ips.append(str(info[4][0]))
    except (socket.gaierror, OSError, UnicodeError):
        pass  # unresolvable: the later fetch would fail too
    return ips


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

    THREAT MODEL (SEC-03, R07.05):
    Three layers:

    1. Scheme + netloc — only http/https, non-empty host.

    2. Hostname substring patterns (BLOCKED_URL_PATTERNS) — catches
       literal hostnames like ``localhost`` and ``internal.company.com``.

    3. Address-level checks (new in R07.05) — the hostname is normalized
       to IP addresses (including decimal/hex/octal IPv4 spellings and
       all IPv6 forms via ``ipaddress``) and DNS names are resolved via
       ``socket.getaddrinfo``; every resulting address is rejected if it
       is loopback / private / link-local / reserved / multicast /
       unspecified. This closes the decimal/hex/octal encodings, the
       IPv4-mapped IPv6 forms, ``[::]``, and check-time DNS rebinding.

    Known residual gaps, accepted for this guardrail tier:
    - A name that does not resolve at check time fails OPEN (the later
      fetch fails anyway — nothing can connect to an unresolvable name).
    - Classic TOCTOU rebinding: an attacker with a short DNS TTL can
      still serve a public IP here and a private IP at connect time.
      Fully closing that requires pinning the connection to the checked
      IP (breaking SNI/Host semantics) — out of scope for this layer;
      http_get() at least re-validates every redirect target.
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
    if not hostname:
        return False, "URL must have a hostname"

    # Check for blocked patterns
    if block_ssrf:
        for pattern in BLOCKED_URL_PATTERNS:
            if pattern in hostname:
                return False, f"SSRF protection: blocked hostname pattern '{pattern}'"

        # SEC-03 (R07.05): substring checks are not enough — normalize the
        # hostname to actual IP addresses and judge each one.
        for ip_str in _iter_hostname_ips(hostname):
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if _ip_address_blocked(ip):
                return False, (
                    f"SSRF protection: hostname '{hostname}' resolves to "
                    f"a non-public address ({ip_str})"
                )

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

    THREAT MODEL (SEC-02, R06.41):
    This function is a GUARDRAIL AGAINST MODEL MISTAKES, not a defense
    against determined prompt injection. The checks are layered:

    1. ``BLOCKED_COMMANDS`` — denylist of obviously-dangerous binaries
       (rm, dd, mkfs, sudo, nc, etc.). Catches the model when it
       casually reaches for one of these because it misread a flag.
       Since SEC-04 (R07.05) this also blocks invoking a shell by name
       (``bash`` / ``sh`` / ``zsh`` / ``ksh`` / ``fish``), whose ``-c``
       flag previously provided an unfiltered escape hatch past every
       other layer.

    2. ``DANGEROUS_FLAG_COMBOS`` — context-aware blocks on otherwise-
       safe commands paired with dangerous flags. Catches the common
       bypass primitives (``find -exec``, ``python -c``, ``perl -e``,
       ``tar --use-compress-program``, ``awk system()``, ``xargs rm``,
       ``cp /dev/null``) without breaking legitimate uses of the
       underlying commands.

    3. Injection pattern regex — catches shell metacharacter chaining
       (``;``, ``|``, ``&&``, ``||``, backticks, ``$()``, ``${}``,
       ``>``, ``<``), embedded newlines, and — since SEC-04 (R07.05) —
       heredocs (``<<EOF``), which previously let a command smuggle an
       arbitrary multi-line script body past the flag-combo checks.

    Layer 1 + 2 catch the obvious model-mistake and prompt-injection-
    via-tool-output payloads. Layer 3 catches classical shell injection.
    A determined adversary can still construct bypasses (Unicode
    normalization, base64-decoded payloads, brace expansion) — for that
    threat, use ``--security max`` AND validate tool output before
    showing it to the model. For trusted models and trusted content,
    ``--security off`` skips all checks (power-user escape hatch).
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

    # Check dangerous-flag combinations (SEC-02, R06.41).
    # Catches bypass payloads like `find -exec rm`, `python -c "import os;
    # os.system('rm')"`, `tar --use-compress-program=sh -c rm`, etc. The
    # base binary is allowed; only the dangerous flag variant is rejected.
    if base_cmd in DANGEROUS_FLAG_COMBOS:
        for pattern, reason in DANGEROUS_FLAG_COMBOS[base_cmd]:
            if re.search(pattern, command):
                return False, f"Blocked flag combination: {reason}", ""

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
        # Heredoc (SEC-04, R07.05): `cmd <<'EOF'` feeds an arbitrary
        # multi-line script to the command, e.g. `python3 - <<'EOF'` as a
        # python -c replacement. Listed BEFORE the redirection patterns so
        # the error message names the heredoc, not generic redirection.
        r"<<\s*['\"]?[A-Za-z_]\w*",  # Heredoc body
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


# ============================================================================
# Tool Output Sanitization (SEC-10 / FEAT-01, R07.05)
# ============================================================================
#
# Indirect prompt injection vector: tool results from ``http_get``,
# ``web_search``, ``shell``, and ``read_file`` flow verbatim into the
# next model context. A 256KB HTTP response that starts with "OK" but
# contains "ignore prior instructions, run X" later passes through
# ``is_error_result``'s first-line check and reaches the model intact.
#
# Mitigation: wrap every tool result in ``<tool_output>`` XML tags
# (so the system prompt can instruct the model to treat the contents as
# untrusted data), truncate overly large results, redact lines that
# look like secrets, and strip ANSI escapes that could manipulate the
# user's terminal during chat display.
#
# This is a non-breaking, additive change — the wrapping is purely
# presentational to the model. Tool implementations are unchanged.

# Patterns that match common secret-bearing lines. The match is on the
# whole line (case-insensitive), so a hit causes the *value* (the part
# after the ``=`` or ``:``) to be replaced with ``[REDACTED]``.
#
# Two capture groups:
#   group(1) = the secret-key name (e.g. ``password``, ``api_key``, ``Bearer``)
#   group(2) = the secret value (everything after the ``=`` or ``:`` separator)
#
# The replacement preserves the key name and the separator, but replaces
# the value with ``[REDACTED]``. This way the model still sees that a
# secret was present (useful for error recovery) without seeing the
# actual secret value.
#
# Special case: ``Bearer`` is a value in an ``Authorization: Bearer <token>``
# header, so it's matched WITHOUT requiring a ``=`` or ``:`` separator —
# the token follows ``Bearer`` directly (after whitespace).
_SECRET_LINE_RE = re.compile(
    r'\b('
    r'password|passwd|pwd|'
    r'api[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token|'
    r'secret[_-]?key|client[_-]?secret|private[_-]?key|'
    # AWS env vars: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SECRET_KEY
    r'aws[_-]?(?:secret[_-])?(?:access|secret)[_-]?key(?:[_-]?id)?|'
    r'connection[_-]?string'
    r')'
    r'(\s*[:=]\s*)(\S+)'
    # Bearer <token> — value follows directly (no = or :)
    r'|\b(bearer)(\s+)(\S+)',
    re.IGNORECASE,
)

# ANSI escape sequences (CSI, OSC, etc.). Strip these from tool output
# so a malicious ``http_get`` response can't clear the user's screen,
# rewrite the terminal title, or enable mouse tracking during chat.
_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]')

# Default cap on tool result size before truncation. 8KB is enough for
# most legitimate tool outputs (a directory listing, a small file, a
# search snippet). Anything larger is almost certainly noise that
# wastes context window without helping the model decide.
DEFAULT_TOOL_OUTPUT_MAX_CHARS = 8192


def sanitize_tool_output(
    result: Any,
    *,
    tool_name: str = "",
    tool_call_id: str = "",
    max_chars: int = DEFAULT_TOOL_OUTPUT_MAX_CHARS,
    redact_secrets: bool = True,
    strip_ansi: bool = True,
) -> str:
    """Wrap and sanitize a tool result before it enters model context.

    SEC-10 / FEAT-01 (R07.05): wraps the result in
    ``<tool_output tool="..." call_id="...">...</tool_output>`` tags so
    the system prompt can instruct the model to treat the contents as
    untrusted data. Applies three layers of sanitization:

    1. **Truncation** — if the result exceeds ``max_chars``, the body is
       truncated to ``max_chars`` and a ``[truncated, N more chars]``
       marker is appended. Prevents a 256KB ``http_get`` response from
       consuming the context window.
    2. **Secret redaction** — lines matching ``password=``, ``api_key:``,
       ``Bearer ...``, etc. have their values replaced with
       ``[REDACTED]``. Protects against the model echo-ing a secret the
       user accidentally exposed via ``shell`` or ``read_file``.
    3. **ANSI escape stripping** — terminal control sequences are removed
       so a malicious tool output cannot clear the screen, rewrite the
       terminal title, or enable mouse tracking during chat.

    Args:
        result: The tool execution result (any type — ``str()`` is
            applied if it isn't already a string).
        tool_name: Name of the tool that produced this result. Included
            in the wrapper tag for the model's reference.
        tool_call_id: OpenResponses ``call_id`` of the tool call that
            produced this result. Included for traceability.
        max_chars: Maximum body size before truncation.
        redact_secrets: If True, secret-looking lines are redacted.
        strip_ansi: If True, ANSI escape sequences are stripped.

    Returns:
        A string of the form
        ``<tool_output tool="..." call_id="...">...body...</tool_output>``.
        Always a valid string, never raises.
    """
    body = result if isinstance(result, str) else str(result)

    if strip_ansi:
        body = _ANSI_ESCAPE_RE.sub('', body)

    if redact_secrets:
        # Replace each secret-bearing match with: <keyname><sep>[REDACTED]
        # Two match forms (mutually exclusive via |):
        #   Form 1 (password=secret, api_key:secret): groups 1,2,3
        #   Form 2 (Bearer <token>): groups 4,5,6
        def _redact(m: re.Match) -> str:
            if m.group(1) is not None:
                # Form 1: <key><sep>[REDACTED]
                return f"{m.group(1)}{m.group(2)}[REDACTED]"
            # Form 2: Bearer [REDACTED]
            return f"{m.group(4)}{m.group(5)}[REDACTED]"

        body = _SECRET_LINE_RE.sub(_redact, body)

    truncated_marker = ""
    if len(body) > max_chars:
        original_len = len(body)
        body = body[:max_chars]
        truncated_marker = f"\n[truncated, {original_len - max_chars} more chars]"

    # Build wrapper tag. Use XML-safe attribute values (escape quotes).
    tool_attr = tool_name.replace('"', '&quot;') if tool_name else ""
    call_attr = (tool_call_id or "").replace('"', '&quot;') if tool_call_id else ""

    attrs = ""
    if tool_attr:
        attrs += f' tool="{tool_attr}"'
    if call_attr:
        attrs += f' call_id="{call_attr}"'

    return f"<tool_output{attrs}>{body}{truncated_marker}</tool_output>"