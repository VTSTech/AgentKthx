"""
⚛️ agentnova - DEPRECATED - Use agentkthx instead

This package has been renamed to 'agentkthx'.
Install: pip install agentkthx
Import: import agentkthx
CLI: agentkthx run "prompt"

This skeleton package exists for backward compatibility only.
It simply re-exports everything from agentkthx.

Repository: https://github.com/VTSTech/AgentKthx
"""

import warnings
import sys

# Show deprecation warning on import
warnings.warn(
    "\n"
    "╔══════════════════════════════════════════════════════════════╗\n"
    "║  'agentnova' has been renamed to 'agentkthx'                ║\n"
    "║                                                              ║\n"
    "║  Please update your imports:                                 ║\n"
    "║      Old: import agentnova                                   ║\n"
    "║      New: import agentkthx                                   ║\n"
    "║                                                              ║\n"
    "║  And your CLI commands:                                      ║\n"
    "║      Old: agentnova run \"prompt\"                             ║\n"
    "║      New: agentkthx run \"prompt\"                             ║\n"
    "║                                                              ║\n"
    "║  Install: pip install agentkthx                              ║\n"
    "║  Repo: https://github.com/VTSTech/AgentKthx                  ║\n"
    "╚══════════════════════════════════════════════════════════════╝\n",
    DeprecationWarning,
    stacklevel=2
)

# Re-export everything from agentkthx
try:
    from agentkthx import *
    from agentkthx import __version__, __author__, __status__
    
    # Re-export version info under agentnova namespace
    __version__ = __version__
    __author__ = __author__
    __status__ = __status__
    
except ImportError:
    raise ImportError(
        "agentkthx is not installed. "
        "Please install it with: pip install agentkthx"
    )


def main():
    """CLI entry point - redirects to agentkthx CLI."""
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  'agentnova' has been renamed to 'agentkthx'                 ║")
    print("║                                                              ║")
    print("║  Redirecting to agentkthx CLI...                             ║")
    print("║                                                              ║")
    print("║  Please use 'agentkthx' command directly in the future.       ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()
    
    # Import and run agentkthx CLI
    from agentkthx.cli import main as agentkthx_main
    return agentkthx_main()


if __name__ == "__main__":
    main()
