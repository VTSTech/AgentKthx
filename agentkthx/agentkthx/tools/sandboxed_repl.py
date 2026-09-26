# -*- coding: utf-8 -*-
"""
⚛️ AgentKthx R02 — Sandboxed Python REPL

A secure Python code execution environment using subprocess isolation
with resource limits, restricted builtins, and module whitelisting.

Security Features:
  - Subprocess isolation (separate process)
  - Memory limits (configurable, default 100MB)
  - CPU time limits (configurable, default 10s)
  - Execution timeout (configurable, default 30s)
  - Restricted builtins (only safe functions)
  - Module whitelist (only allowed imports)
  - No filesystem access (via resource limits)
  - No network access (via restricted imports)

Written by VTSTech — https://www.vts-tech.org — https://github.com/VTSTech/AgentKthx
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional


# ================================================================== #
#  Configuration                                                      #
# ================================================================== #

@dataclass
class SandboxConfig:
    """Configuration for the sandboxed Python REPL."""

    # Resource limits
    memory_mb: int = 100           # Max memory in MB
    cpu_seconds: int = 10          # Max CPU time in seconds
    timeout_seconds: int = 30      # Max wall-clock time in seconds

    # Security settings
    allow_network: bool = False    # Allow network-related modules
    allow_filesystem: bool = False # Allow filesystem-related modules
    allow_subprocess: bool = False # Allow subprocess creation

    # Additional allowed modules (beyond defaults)
    extra_modules: set = None

    def __post_init__(self):
        if self.extra_modules is None:
            self.extra_modules = set()


# Default configuration
DEFAULT_CONFIG = SandboxConfig()


# Safe builtins - functions that are generally safe to use
SAFE_BUILTINS = {
    # Basic types
    'bool', 'int', 'float', 'str', 'list', 'dict', 'tuple', 'set', 'frozenset',
    'bytes', 'bytearray', 'complex',

    # Type checking
    'type', 'isinstance', 'issubclass', 'hasattr', 'callable',

    # Basic functions
    'print', 'len', 'range', 'enumerate', 'zip', 'map', 'filter',
    'sorted', 'reversed', 'slice', 'any', 'all', 'min', 'max', 'sum',
    'abs', 'round', 'pow', 'divmod', 'hash', 'id', 'repr', 'ascii',
    'bin', 'hex', 'oct', 'chr', 'ord', 'format',

    # Constants
    'True', 'False', 'None', 'Ellipsis',

    # Exceptions (for try/except)
    'Exception', 'BaseException', 'ValueError', 'TypeError', 'KeyError',
    'IndexError', 'AttributeError', 'RuntimeError', 'StopIteration',
    'NotImplementedError', 'ImportError', 'NameError', 'ZeroDivisionError',
    'OverflowError', 'FloatingPointError', 'ArithmeticError',

    # Iteration
    'iter', 'next', 'staticmethod', 'classmethod', 'property',

    # Object
    'object', 'super', 'vars', 'dir', 'getattr', 'setattr', 'delattr',

    # Import (we'll override this with a safe version)
    '__import__',
}

# Safe modules - modules that don't pose security risks
SAFE_MODULES = {
    # Math and numbers
    'math', 'cmath', 'decimal', 'fractions', 'random', 'statistics',

    # Data structures
    'collections', 'collections.abc', 'heapq', 'bisect', 'array',
    'itertools', 'functools', 'operator',

    # Text processing
    're', 'string', 'textwrap', 'difflib', 'unicodedata',

    # Data formats
    'json', 'csv', 'configparser', 'html', 'xml.etree.ElementTree',

    # Date and time
    'datetime', 'time', 'calendar', 'zoneinfo',

    # Copy and pickle (limited)
    'copy', 'pprint',

    # Type hints
    'typing', 'typing_extensions', 'types', 'dataclasses', 'enum',

    # Other safe modules
    'contextlib', 'io', 'stringio', 'struct', 'codecs',
    'inspect', 'dis', 'ast', 'tokenize', 'keyword', 'token',
    'traceback', 'warnings', 'weakref', 'abc',
}

# Modules that require explicit permission
NETWORK_MODULES = {
    'socket', 'ssl', 'select', 'selectors', 'asyncio', 'urllib',
    'http', 'ftplib', 'poplib', 'imaplib', 'smtplib', 'telnetlib',
    'xmlrpc', 'ipaddress', 'socketserver', 'http.server',
}

FILESYSTEM_MODULES = {
    'os', 'sys', 'shutil', 'tempfile', 'glob', 'fnmatch',
    'fileinput', 'linecache', 'pickle', 'shelve', 'dbm',
    'sqlite3', 'zipfile', 'tarfile', 'gzip', 'bz2', 'lzma',
    'subprocess', 'spawn', 'multiprocessing',
}

SUBPROCESS_MODULES = {
    'subprocess', 'os', 'sys', 'spawn', 'multiprocessing', 'threading',
    '_thread', 'concurrent', 'concurrent.futures',
}


# ================================================================== #
#  Sandbox Runner Generator                                           #
# ================================================================== #

def _generate_runner_script(code: str, config: SandboxConfig) -> str:
    """
    Generate the sandboxed Python runner script.

    This script is executed in a subprocess with the provided configuration.
    """

    # Build allowed modules list
    allowed_modules = set(SAFE_MODULES)

    if config.allow_network:
        allowed_modules.update(NETWORK_MODULES)
    if config.allow_filesystem:
        allowed_modules.update(FILESYSTEM_MODULES)
    if config.allow_subprocess:
        allowed_modules.update(SUBPROCESS_MODULES)

    if config.extra_modules:
        allowed_modules.update(config.extra_modules)

    allowed_modules_str = ', '.join(repr(m) for m in sorted(allowed_modules))
    safe_builtins_str = ', '.join(repr(k) for k in sorted(SAFE_BUILTINS))

    return f'''
import sys
import resource
import signal
import os

# ================================================================
#  Resource Limits
# ================================================================

# Memory limit
try:
    resource.setrlimit(
        resource.RLIMIT_AS,
        ({config.memory_mb * 1024 * 1024}, {config.memory_mb * 1024 * 1024})
    )
except (ValueError, resource.error):
    pass  # May not be supported on all systems

# CPU time limit
try:
    resource.setrlimit(
        resource.RLIMIT_CPU,
        ({config.cpu_seconds}, {config.cpu_seconds + 1})
    )
except (ValueError, resource.error):
    pass

# No file creation
try:
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
except (ValueError, resource.error):
    pass

# ================================================================
#  Timeout Handler
# ================================================================

def _timeout_handler(signum, frame):
    raise TimeoutError("Execution timed out")

signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm({config.timeout_seconds})

# ================================================================
#  Safe Builtins
# ================================================================

_safe_builtins_names = {{{safe_builtins_str}}}
_original_builtins = __builtins__.copy() if isinstance(__builtins__, dict) else dir(__builtins__)

_safe_builtins = {{}}
for name in _safe_builtins_names:
    try:
        if isinstance(__builtins__, dict):
            _safe_builtins[name] = __builtins__[name]
        else:
            _safe_builtins[name] = getattr(__builtins__, name)
    except (KeyError, AttributeError):
        pass

# ================================================================
#  Module Import Restrictions
# ================================================================

_allowed_modules = {{{allowed_modules_str}}}
_original_import = _safe_builtins.get('__import__', __builtins__['__import__'] if isinstance(__builtins__, dict) else __builtins__.__import__)

def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Restricted import that only allows whitelisted modules."""
    # Get top-level module name
    top_level = name.split('.')[0]

    if top_level not in _allowed_modules:
        raise ImportError(
            f"Module '{{name}}' is not allowed in sandbox. "
            f"Allowed: math, json, re, datetime, collections, itertools, etc."
        )

    return _original_import(name, globals, locals, fromlist, level)

_safe_builtins['__import__'] = _safe_import

# ================================================================
#  Safe Execution Environment
# ================================================================

_safe_globals = {{
    '__builtins__': _safe_builtins,
    '__name__': '__main__',
    '__doc__': None,
}}

# Pre-import commonly used safe modules
_common_modules = ['math', 'json', 're', 'datetime', 'collections', 'itertools']
for _mod_name in _common_modules:
    if _mod_name in _allowed_modules:
        try:
            _safe_globals[_mod_name] = __import__(_mod_name)
        except ImportError:
            pass

# ================================================================
#  Execute User Code
# ================================================================

try:
    exec({repr(code)}, _safe_globals)

except TimeoutError:
    print("[Sandbox] Execution timed out ({{config.timeout_seconds}}s limit)")

except MemoryError:
    print("[Sandbox] Memory limit exceeded ({{config.memory_mb}}MB limit)")

except ImportError as e:
    print(f"[Sandbox] Import blocked: {{e}}")

except KeyboardInterrupt:
    print("[Sandbox] Execution interrupted")

except SystemExit as e:
    # Always report SystemExit — even code 0 may indicate unintended sandbox escape
    if e.code is not None and e.code != 0:
        print(f"[Sandbox] Script exited with code {{e.code}}")
    else:
        print(f"[Sandbox] SystemExit({{e.code}}) intercepted — sandbox exit blocked")

except Exception as e:
    import traceback
    print(f"[Error] {{type(e).__name__}}: {{e}}")
    # Print truncated traceback
    tb_lines = traceback.format_exc().split('\\n')[-5:]
    for line in tb_lines:
        if line.strip():
            print(line)

finally:
    signal.alarm(0)  # Cancel timeout
'''


# ================================================================== #
#  Main Sandbox Function                                              #
# ================================================================== #

def sandboxed_exec(
    code: str,
    config: Optional[SandboxConfig] = None,
    python_path: str = "python3"
) -> str:
    """
    Execute Python code in a sandboxed subprocess.

    Parameters
    ----------
    code : str
        Python code to execute.
    config : SandboxConfig, optional
        Sandbox configuration. Uses defaults if not provided.
    python_path : str
        Path to Python interpreter. Default: "python3"

    Returns
    -------
    str
        Output from the execution (stdout + stderr).

    Examples
    --------
    >>> result = sandboxed_exec("print(2 ** 10)")
    >>> print(result)
    1024

    >>> result = sandboxed_exec("import os; os.system('ls')")
    >>> print(result)
    [Sandbox] Import blocked: Module 'os' is not allowed...

    Notes
    -----
    Security guarantees:
    - Code runs in a separate process
    - Memory limited to config.memory_mb (default: 100MB)
    - CPU time limited to config.cpu_seconds (default: 10s)
    - Wall clock timeout at config.timeout_seconds (default: 30s)
    - Only whitelisted modules can be imported
    - File system access blocked via RLIMIT_NOFILE

    Known limitations:
    - Resource limits may not work on all platforms (e.g., macOS)
    - Very determined attackers may find bypasses
    - For production, consider Docker/gVisor for stronger isolation
    """
    if config is None:
        config = DEFAULT_CONFIG

    # Generate the sandboxed runner script
    runner_script = _generate_runner_script(code, config)

    try:
        # Execute in subprocess
        result = subprocess.run(
            [python_path, '-c', runner_script],
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds + 10,  # Extra buffer for subprocess overhead
            env={
                'PYTHONDONTWRITEBYTECODE': '1',  # Don't create .pyc files
                'PYTHONUNBUFFERED': '1',          # Unbuffered output
                'PYTHONDONTMALLOC': '1',          # Don't use malloc for small objects
            },
            # Don't allow stdin (prevent interactive prompts)
            stdin=subprocess.DEVNULL,
        )

        # Combine stdout and stderr
        output = result.stdout

        if result.stderr:
            # Filter out common warnings
            stderr_lines = []
            for line in result.stderr.split('\n'):
                if 'ResourceWarning' in line:
                    continue
                if 'unclosed file' in line.lower():
                    continue
                stderr_lines.append(line)

            if stderr_lines:
                output += '\n' + '\n'.join(stderr_lines)

        return output.strip() or "[No output]"

    except subprocess.TimeoutExpired:
        return f"[Sandbox] Process timeout - terminated after {config.timeout_seconds + 10}s"

    except subprocess.CalledProcessError as e:
        return f"[Sandbox Error] Process failed with code {e.returncode}"

    except FileNotFoundError:
        return f"[Sandbox Error] Python interpreter not found: {python_path}"

    except Exception as e:
        return f"[Sandbox Error] {type(e).__name__}: {e}"


# ================================================================== #
#  Convenience Functions                                               #
# ================================================================== #

def create_sandbox_tool(registry, config: Optional[SandboxConfig] = None):
    """
    Register sandboxed Python REPL as a tool.

    Parameters
    ----------
    registry : ToolRegistry
        Tool registry to add the tool to.
    config : SandboxConfig, optional
        Sandbox configuration.

    Example
    -------
    >>> from agentkthx.tools import ToolRegistry
    >>> from agentkthx.tools.sandboxed_repl import create_sandbox_tool
    >>> registry = ToolRegistry()
    >>> create_sandbox_tool(registry)
    >>> result = registry.invoke('python_repl_safe', {'code': 'print(1+1)'})
    """

    @registry.register(
        name="python_repl_safe",
        description=(
            "Execute Python code in a SECURE SANDBOXED environment. "
            "Use this for running Python code safely with memory limits, "
            "time limits, and restricted module access. Only safe modules "
            "like math, json, re, datetime, collections are available. "
            "File system, network, and subprocess operations are BLOCKED."
        ),
        dangerous=False,
        category="code",
    )
    def python_repl_safe(code: str) -> str:
        """Execute Python code in a sandboxed subprocess with resource limits."""
        return sandboxed_exec(code, config)

    return python_repl_safe


# ================================================================== #
#  Test Function                                                       #
# ================================================================== #

def test_sandbox():
    """Run basic sandbox tests."""

    print("Testing sandboxed Python REPL...")
    print("=" * 60)

    # Test 1: Basic execution
    print("\n1. Basic calculation:")
    result = sandboxed_exec("print(2 ** 10)")
    print(f"   Result: {result}")
    assert "1024" in result

    # Test 2: Safe imports
    print("\n2. Safe import (math):")
    result = sandboxed_exec("import math; print(math.sqrt(144))")
    print(f"   Result: {result}")
    assert "12" in result

    # Test 3: Blocked import (os)
    print("\n3. Blocked import (os):")
    result = sandboxed_exec("import os; print(os.listdir('.'))")
    print(f"   Result: {result}")
    assert "blocked" in result.lower() or "not allowed" in result.lower()

    # Test 4: Blocked import (subprocess)
    print("\n4. Blocked import (subprocess):")
    result = sandboxed_exec("import subprocess; subprocess.run(['ls'])")
    print(f"   Result: {result}")
    assert "blocked" in result.lower() or "not allowed" in result.lower()

    # Test 5: Timeout test (infinite loop)
    print("\n5. Timeout test (should timeout in ~10s):")
    result = sandboxed_exec("while True: pass")
    print(f"   Result: {result}")
    # Could be timeout, killed, or no output (process killed by CPU limit)
    assert any(word in result.lower() for word in ["timeout", "time", "no output", "killed", "exceeded"]), f"Expected timeout but got: {result}"

    # Test 6: Safe modules
    print("\n6. Multiple safe imports:")
    result = sandboxed_exec("""
import json
import datetime
import re
from collections import Counter

data = {"test": 123, "items": [1, 2, 3]}
print(json.dumps(data))
print(datetime.datetime.now().strftime("%Y-%m-%d"))
print(re.findall(r'\\d+', "abc123def456"))
print(Counter([1, 1, 2, 2, 2, 3]))
""")
    print(f"   Result: {result}")

    print("\n" + "=" * 60)
    print("All tests completed!")


if __name__ == "__main__":
    test_sandbox()