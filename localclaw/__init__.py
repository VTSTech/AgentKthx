"""
⚛️ LocalClaw → AgentKthx Redirect

This package has been renamed to AgentKthx.
All functionality is now available under the 'agentkthx' package.

Status: Alpha

Written by VTSTech — https://www.vts-tech.org

Migration Guide:
    # Old (deprecated)
    from localclaw import Agent
    
    # New (recommended)
    from agentkthx import Agent

CLI:
    # Both commands work identically
    localclaw run "What is 2+2?"
    agentkthx run "What is 2+2?"
"""

import sys
import warnings

# Issue deprecation warning
warnings.warn(
    "The 'localclaw' package has been renamed to 'agentkthx'. "
    "Please update your imports: 'from agentkthx import ...' instead of 'from localclaw import ...'",
    DeprecationWarning,
    stacklevel=2
)

# Re-export everything from agentkthx
from agentkthx import *
from agentkthx import __version__, __author__, __status__


def main():
    """Entry point for localclaw CLI command."""
    from agentkthx.cli import main as agentkthx_main
    return agentkthx_main()


__all__ = [
    "__version__",
    "__author__",
    "__status__",
    "main",
]
